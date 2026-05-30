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
from stackowl.pipeline.services import get_services
from stackowl.tools.base import Tool, ToolManifest, ToolResult

# Sane cap on predefined choices. On chat channels typing always works (and a
# tapped button resolves with the exact choice TEXT), so no synthetic escape-hatch
# pseudo-choice is appended — the choices are passed through unchanged, capped.
_MAX_CHOICES = 5

# Default park timeout: a parked asyncio waiter is cheap and the concurrent
# gateway loop frees the loop while we wait, so 30 minutes is comfortable.
_DEFAULT_TIMEOUT_S = 1800.0

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

# Sentinel for non-interactive contexts. ABORTS on a consequential gate; never
# assumes consent (party Security).
_NON_INTERACTIVE_SENTINEL = (
    "Cannot ask the user in this non-interactive context (cron/parliament/"
    "delegation). If this was gating a consequential action, ABORT — do not "
    "assume an answer; otherwise proceed with your best assumption and state it."
)


class ClarifyTool(Tool):
    """Ask the user a question mid-turn and BLOCK until they answer (or timeout)."""

    def __init__(self, *, timeout_s: float = _DEFAULT_TIMEOUT_S) -> None:
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
            "LANE: resolve ambiguity / missing info by asking the human. "
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
            answer, timed_out = await gateway.wait_for_answer(
                clarify_id, timeout=self._timeout_s,
            )
        except Exception as exc:  # self-healing — never raise out of a tool
            log.tool.error(
                "clarify.execute: gateway ask/wait failed — degrading",
                exc_info=exc,
                extra={"_fields": {"channel": channel, "session_id": session_id}},
            )
            return self._unavailable(t0)

        # 4. EXIT — graceful in-turn timeout, or the answer to continue the turn.
        if timed_out:
            return self._ok(
                _TIMED_OUT.format(question=question),
                t0,
                extra={"clarify_id": clarify_id, "timed_out": True},
            )
        return self._ok(
            _ANSWERED.format(question=question, answer=answer),
            t0,
            extra={"clarify_id": clarify_id, "answered": True},
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
