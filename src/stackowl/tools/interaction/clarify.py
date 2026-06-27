"""clarify — ask the USER a question mid-turn and BLOCK until they answer.

The owl calls ``clarify`` when the task is genuinely ambiguous or missing
information only the user can supply. The question is delivered to the user's
channel and the tool PARKS on an asyncio waiter mid-turn (blocking-await — the
PRIMARY mode). When the user's reply arrives the gateway loop routes it to
``ClarifyGateway.try_resolve``, which wakes the parked waiter IN THE SAME TURN
and the tool returns the user's answer as its output, so the model continues
the original turn with the answer in hand. Turn-yield (the tool returning a
"stop and wait" sentinel and the next message resuming in a fresh turn) remains
a documented fallback at the gateway layer.

If the user does not reply within the (generous) timeout, the tool returns a
structured graceful-timeout result telling the model to ABORT if the clarify was
gating a consequential action (never assume consent — party Security), or
otherwise proceed with a stated best assumption.

Non-interactive contexts (cron / parliament / delegation — ``interactive`` is
False on the TraceContext) CANNOT ask a user: there is nobody to answer. In that
case the tool returns a structured SENTINEL (same ABORT-or-assume contract) and
never parks anything.

Severity: ``read`` — asking a question mutates nothing. ``toolset_group``:
``interaction``.

Provenance: the choices affordance and the cron-cannot-ask rule are ported
HYBRID from a reference agent's clarify primitive. See
``_bmad-output/research/tool-port-analysis.md`` (E5 ``clarify`` row).
"""

from __future__ import annotations

import time

from stackowl.infra.observability import log
from stackowl.infra.trace import TraceContext
from stackowl.interaction.clarify_gateway import (
    CLARIFY_TTL_SECONDS,
    OUTCOME_ANSWERED,
    OUTCOME_CANCELLED,
    ClarifyGateway,
)
from stackowl.pipeline.services import get_services
from stackowl.tools.base import Tool, ToolManifest, ToolResult

# Sane cap on predefined choices. On chat channels typing always works (and a
# tapped button resolves with the exact choice TEXT), so no synthetic escape-hatch
# pseudo-choice is appended — the choices are passed through unchanged, capped.
_MAX_CHOICES = 5

# Answer frame surfaced to the model when the user replies, so it continues the
# original turn with the answer in hand (party LLM-Ergonomics).
_ANSWERED = "The user answered your question ({question!r}): {answer}"

# Graceful in-turn timeout result. ABORTS on a consequential gate; never assumes
# consent (party Security).
_TIMED_OUT = (
    "The user did not reply in time to your question ({question!r}). If this was "
    "gating a consequential action, ABORT — do not assume an answer; otherwise "
    "proceed with your best assumption and state it."
)

# Graceful in-turn timeout for a REVERSIBLE clarify that carried a SAFE default
# (F-68): auto-resume with the stated assumption instead of punting the whole
# decision back to the prompt and burning the full TTL. Only reached for a
# low-stakes gate with a menu-consistent default — an irreversible/high-stakes
# clarify keeps the ABORT path above (never assume consent — party Security).
_TIMED_OUT_DEFAULTED = (
    "The user did not reply in time to your question ({question!r}). This was a "
    "reversible choice with a safe default, so proceed with the assumed answer: "
    "{default!r}. State this assumption plainly in your reply so the user can "
    "correct it if it was wrong."
)

# Distinct PIVOT result: the user moved on to a different request without
# answering. NOT a timeout — set the question aside, do NOT assume or act on any
# answer to it (party Security: a pivot is not consent and not an assumption).
_CANCELLED = (
    "The user moved on to a different request without answering your question "
    "({question!r}). Acknowledge in ONE short line that you're setting that "
    "question aside; do not block, and do NOT assume or act on any answer to it."
)

# Sentinel for non-interactive contexts. ABORTS on a consequential gate; never
# assumes consent (party Security).
_NON_INTERACTIVE_SENTINEL = (
    "Cannot ask the user in this non-interactive context (cron/parliament/"
    "delegation). If this was gating a consequential action, ABORT — do not "
    "assume an answer; otherwise proceed with your best assumption and state it."
)


class ClarifyTool(Tool):
    """Ask the user a question mid-turn and BLOCK until they answer (or timeout)."""

    def __init__(self, *, timeout_s: float = CLARIFY_TTL_SECONDS) -> None:
        """Store the park timeout (seconds) the interactive path waits on.

        Defaults to 30 minutes — a parked asyncio waiter is cheap and the
        concurrent gateway loop frees the loop while we wait. Overridable (tests
        pass a tiny value to exercise the graceful-timeout path).
        """
        self._timeout_s = timeout_s

    @property
    def name(self) -> str:
        return "clarify"

    @property
    def description(self) -> str:
        return (
            "Ask the USER a question and WAIT for their answer, when the task is "
            "genuinely ambiguous or you are missing information only the user "
            "can provide. Provide 'choices' (a short list) when the answer is a "
            "pick from options; omit them for an open question (set "
            "awaiting_text). After calling this "
            "you MUST stop and wait — the user's next message is their answer. "
            "LANE: resolve ambiguity / missing info by asking the human, but ONLY "
            "when the action it gates is irreversible or expensive. When the most "
            "likely action is reversible or cheap, do NOT clarify: act on the most "
            "likely interpretation and state your assumption. "
            "ANTI-LANE: do NOT use clarify to look something up — use memory for "
            "durable facts, session_search for what was said before, or "
            "skill_view for a procedure. Do not ask the user what you can find "
            "yourself."
        )

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The question to ask the user.",
                },
                "choices": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": _MAX_CHOICES,
                    "description": (
                        f"Up to {_MAX_CHOICES} answer choices, passed through "
                        "verbatim (no synthetic option is added). Omit for an "
                        "open-ended question."
                    ),
                },
                "awaiting_text": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "Hint that a free-text answer is expected (e.g. an "
                        "open-ended question or an 'Other' pick)."
                    ),
                },
                "default": {
                    "type": "string",
                    "description": (
                        "Optional safe fallback to assume if the user does NOT "
                        "reply in time. Provide it ONLY for a REVERSIBLE choice "
                        "(must be one of 'choices' when choices are given). On "
                        "timeout the tool auto-resumes with this assumption "
                        "instead of stalling. Omit for an irreversible/expensive "
                        "gate — those abort on timeout, never assume consent."
                    ),
                },
                "high_stakes": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "Set True when the gated action is irreversible or "
                        "expensive. Suppresses any timeout auto-default so the "
                        "tool aborts rather than assuming an answer."
                    ),
                },
            },
            "required": ["question"],
        }

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
            action_severity="read",
            toolset_group="interaction",
        )

    # --------------------------------------------------------------- execute

    async def execute(self, **kwargs: object) -> ToolResult:
        t0 = time.monotonic()
        question = str(kwargs.get("question", "")).strip()
        # 1. ENTRY
        log.tool.info(
            "clarify.execute: entry",
            extra={"_fields": {"question_len": len(question)}},
        )

        if not question:
            return self._err("clarify requires a non-empty 'question'.", t0)

        ctx = TraceContext.get()
        interactive = bool(ctx.get("interactive", False))
        channel = ctx.get("channel")
        session_id = ctx.get("session_id")

        # 2. DECISION — non-interactive contexts cannot ask anyone. Sentinel
        # (aborts on a consequential gate); never pauses, never assumes consent.
        if not interactive:
            log.tool.info(
                "clarify.execute: non-interactive context — sentinel deny",
                extra={"_fields": {"channel": channel, "session_id": session_id}},
            )
            return self._ok(_NON_INTERACTIVE_SENTINEL, t0, extra={"denied": "non_interactive"})

        # Need both a channel to deliver on and a session to bind the resolution.
        if not session_id or not channel:
            return self._err(
                "Cannot ask the user: no channel context (missing session/channel). "
                "Proceed with your best assumption and state it.",
                t0,
            )

        gateway = get_services().clarify_gateway
        if gateway is None:
            # Self-healing: degrade to a structured result, never raise.
            return self._unavailable(t0)

        choices = self._coerce_choices(kwargs.get("choices"))
        awaiting_text = bool(kwargs.get("awaiting_text", False))
        declared_default = self._coerce_default(kwargs.get("default"))
        high_stakes = bool(kwargs.get("high_stakes", False))

        try:
            # 3. STEP — register + deliver as a BLOCKING ask, then park on the
            # waiter until the user's reply wakes us in-turn (or we time out).
            clarify_id = await gateway.ask(
                str(session_id),
                str(channel),
                question,
                choices=choices,
                awaiting_text=awaiting_text,
                blocking=True,
            )
            answer, outcome = await gateway.wait_for_answer(
                clarify_id, timeout=self._timeout_s,
            )
        except Exception as exc:  # self-healing — never raise out of a tool
            log.tool.error(
                "clarify.execute: gateway ask/wait failed — degrading",
                exc_info=exc,
                extra={"_fields": {"channel": channel, "session_id": session_id}},
            )
            return self._unavailable(t0)

        # 4. EXIT — one of three DISTINCT outcomes: the answer (continue the turn),
        # a user PIVOT (set the question aside, no assumption), or a graceful
        # in-turn timeout (abort on a consequential gate, else best assumption).
        if outcome == OUTCOME_ANSWERED:
            return self._ok(
                _ANSWERED.format(question=question, answer=answer),
                t0,
                extra={"clarify_id": clarify_id, "answered": True},
            )
        if outcome == OUTCOME_CANCELLED:
            return self._ok(
                _CANCELLED.format(question=question),
                t0,
                extra={"clarify_id": clarify_id, "cancelled": True},
            )
        # Graceful in-turn timeout. F-68: for a REVERSIBLE clarify carrying a SAFE
        # default (a one-item menu or a declared default consistent with it),
        # auto-resume with the stated assumption instead of punting the whole
        # decision back and burning the TTL. A high-stakes/irreversible gate (or no
        # safe default) keeps the ABORT-on-consequential punt — never assume consent.
        assumed = self._safe_timeout_default(choices, declared_default, high_stakes)
        if assumed is not None:
            log.tool.info(
                "clarify.execute: timeout — auto-resuming reversible clarify with default",
                extra={"_fields": {"clarify_id": clarify_id, "high_stakes": high_stakes}},
            )
            return self._ok(
                _TIMED_OUT_DEFAULTED.format(question=question, default=assumed),
                t0,
                extra={"clarify_id": clarify_id, "timed_out": True, "defaulted": True},
            )
        return self._ok(
            _TIMED_OUT.format(question=question),
            t0,
            extra={"clarify_id": clarify_id, "timed_out": True},
        )

    # ---------------------------------------------------------------- helpers

    @staticmethod
    def _coerce_choices(raw: object) -> tuple[str, ...]:
        """Trim blank entries and cap at ``_MAX_CHOICES`` — passed through as-is."""
        if not isinstance(raw, (list, tuple)):
            return ()
        items = [str(c).strip() for c in raw if str(c).strip()]
        if not items:
            return ()
        return tuple(items[:_MAX_CHOICES])

    @staticmethod
    def _coerce_default(raw: object) -> str | None:
        """Normalise an optional ``default`` arg to a non-blank string, or ``None``."""
        if not isinstance(raw, str):
            return None
        value = raw.strip()
        return value or None

    @staticmethod
    def _safe_timeout_default(
        choices: tuple[str, ...], declared_default: str | None, high_stakes: bool,
    ) -> str | None:
        """Resolve a SAFE timeout fallback (F-68), or ``None`` to keep the ABORT punt.

        Delegates to the gateway's single source of truth for the reversible-default
        policy (no duplicated/hardcoded rule): a fallback exists only for a low-stakes
        clarify with a one-item menu or a declared default consistent with the menu.
        A ``high_stakes`` (irreversible) gate always yields ``None`` → ABORT.
        """
        return ClarifyGateway._resolve_default(
            choices=choices, default=declared_default, high_stakes=high_stakes,
        )

    @staticmethod
    def _ok(
        output: str, t0: float, *, extra: dict[str, object] | None = None,
    ) -> ToolResult:
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.info(
            "clarify.execute: exit",
            extra={"_fields": {"success": True, "duration_ms": duration_ms, **(extra or {})}},
        )
        return ToolResult(success=True, output=output, duration_ms=duration_ms)

    @staticmethod
    def _err(msg: str, t0: float) -> ToolResult:
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.info(
            "clarify.execute: exit",
            extra={"_fields": {"success": False, "error": msg, "duration_ms": duration_ms}},
        )
        return ToolResult(success=False, output="", error=msg, duration_ms=duration_ms)

    @staticmethod
    def _unavailable(t0: float) -> ToolResult:
        """Self-healing: no clarify_gateway wired → structured 'unavailable'."""
        msg = "clarify unavailable: no clarify gateway is configured."
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.warning(
            "clarify.execute: gateway unavailable — structured degradation",
            extra={"_fields": {"duration_ms": duration_ms}},
        )
        return ToolResult(success=False, output="", error=msg, duration_ms=duration_ms)
