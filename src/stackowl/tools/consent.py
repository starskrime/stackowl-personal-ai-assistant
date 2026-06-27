"""Consent policy — the combination consent UX for consequential tool actions.

Provenance: port-before-build research is recorded in
``_bmad-output/research/tool-port-analysis.md``. The adopted prior-art pattern
is a *scope-returning approval callback* (the user grants one of
once/session/window/deny) coordinated through a *background-resolved block*:
the acting coroutine suspends until a separate handler resolves the request.
StackOwl implements that coordination natively on ``asyncio.Future`` rather
than thread events + watchdog polling.

The combination policy itself — trust tiers + session batch + time-window
grants + always-ask exclusions, all audited — is built for StackOwl and is
operator-voted (readiness-check.md Section 9, decisions #1 and #7); prior art
gates a single action per prompt with only session/permanent scopes.

Channel-agnostic by design: the actual prompt UX is supplied via a
:class:`ConsentPrompter` (CLI stdin, Telegram inline keyboard, …). With no
prompter wired the policy fails CLOSED — silence is never consent.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, runtime_checkable

from stackowl.infra.clock import Clock, WallClock
from stackowl.infra.observability import log
from stackowl.interaction.reversibility_resolver import (
    Decision,
    Reversibility,
    ReversibilityResolver,
    reversibility_resolver_enabled,
)

__all__ = [
    "ConsentScope",
    "TrustTier",
    "ConsentRequest",
    "ConsentPrompter",
    "FailClosedPrompter",
    "RoutingPrompter",
    "TtyConsentPrompter",
    "ConsentPolicy",
]

# Default tools that ALWAYS re-prompt — never satisfied by batch/window/auto.
# Per E11/E12/E13 party reviews (readiness Section 9 #7): code execution, GUI
# control, and Home-Assistant locks/alarms are never relaxed.
_DEFAULT_ALWAYS_ASK_TOOLS = frozenset(
    {"execute_code", "computer_use", "ha_call_service", "browser_dialog"}
)
_DEFAULT_ALWAYS_ASK_CATEGORIES = frozenset({"lock", "alarm", "destructive"})
_DEFAULT_WINDOW_SECONDS = 900.0  # 15-minute trust window


class ConsentScope(StrEnum):
    """The scope a user grants when approving a consequential action."""

    ONCE = "once"  # allow this single action; re-prompt next time
    SESSION = "session"  # allow all of this tool for the rest of the session
    WINDOW = "window"  # allow this tool for a time window
    DENY = "deny"  # refuse
    DENY_SESSION = "deny_session"  # refuse this and all of this tool for the session


class TrustTier(StrEnum):
    """Per-tool configured trust level (the policy default is ALWAYS_ASK)."""

    ALWAYS_ASK = "always_ask"  # prompt every time (honoring prior batch/window grants)
    ASK_ONCE_SESSION = "ask_once_session"  # prompt once per session, then auto for session
    AUTO = "auto"  # auto-allow without prompting (never for excluded tools)
    NEVER = "never"  # hard block, never prompt


@dataclass(frozen=True)
class ConsentRequest:
    """A single consent request handed to a :class:`ConsentPrompter`."""

    tool_name: str
    channel: str
    session_id: str
    category: str | None = None
    summary: str = ""
    # When False, the prompter must NOT offer batch/window relaxation buttons
    # (the tool/category is on the always-ask exclusion list).
    allow_relaxation: bool = True
    # F-27 — the gated effect is low-blast-radius REVERSIBLE (locally owned +
    # rollback-able). Informational for the prompter; the policy uses it to
    # auto-allow-with-undo. Defaults False (when in doubt, ask).
    reversible: bool = False


@runtime_checkable
class ConsentPrompter(Protocol):
    """Channel-specific UX that asks the user and returns the granted scope."""

    async def prompt(self, req: ConsentRequest) -> ConsentScope: ...


class FailClosedPrompter:
    """Default prompter — denies everything. Used when no channel UX is wired."""

    async def prompt(self, req: ConsentRequest) -> ConsentScope:
        log.tool.warning(
            "[consent] FailClosedPrompter: no channel UX — denying",
            extra={"_fields": {"tool": req.tool_name, "channel": req.channel}},
        )
        return ConsentScope.DENY


class RoutingPrompter:
    """Multiplexes consent requests to a per-channel prompter; unknown → deny."""

    def __init__(self) -> None:
        self._by_channel: dict[str, ConsentPrompter] = {}

    def register(self, channel: str, prompter: ConsentPrompter) -> None:
        log.tool.debug(
            "[consent] RoutingPrompter.register",
            extra={"_fields": {"channel": channel}},
        )
        self._by_channel[channel] = prompter

    async def prompt(self, req: ConsentRequest) -> ConsentScope:
        prompter = self._by_channel.get(req.channel)
        if prompter is None:
            log.tool.warning(
                "[consent] RoutingPrompter: no prompter for channel — fail closed",
                extra={"_fields": {"channel": req.channel, "tool": req.tool_name}},
            )
            return ConsentScope.DENY
        return await prompter.prompt(req)


class TtyConsentPrompter:
    """CLI prompter — asks on stdin off the event loop. Fails closed off-TTY.

    Returns SESSION when the user types the batch keyword, otherwise ONCE on a
    yes and DENY on anything else. Relaxation keywords are ignored for
    always-ask tools (``req.allow_relaxation is False``).
    """

    # Affirmative / batch tokens are matched case-insensitively against trimmed
    # input. These are control tokens, not user-facing copy.
    _AFFIRM = frozenset({"y", "yes"})
    _BATCH = frozenset({"a", "all", "always"})

    async def prompt(self, req: ConsentRequest) -> ConsentScope:
        if not sys.stdin.isatty():
            log.tool.warning(
                "[consent] TtyConsentPrompter: non-interactive stdin — fail closed",
                extra={"_fields": {"tool": req.tool_name}},
            )
            return ConsentScope.DENY
        relax = " [y/N/all]" if req.allow_relaxation else " [y/N]"
        prompt_line = f"⚠ {req.tool_name}: {req.summary}{relax} "
        try:
            answer = (await asyncio.to_thread(input, prompt_line)).strip().lower()
        except Exception as exc:  # EOF, closed stdin, etc. → fail closed
            log.tool.error(
                "[consent] TtyConsentPrompter: input failed — fail closed",
                exc_info=exc,
                extra={"_fields": {"tool": req.tool_name}},
            )
            return ConsentScope.DENY
        if req.allow_relaxation and answer in self._BATCH:
            return ConsentScope.SESSION
        if answer in self._AFFIRM:
            return ConsentScope.ONCE
        return ConsentScope.DENY


@dataclass
class ConsentPolicy:
    """Decides whether a consequential action may proceed (the combination UX).

    State (session batch + time-window grants) is in-memory and ephemeral on
    purpose: a restart must NOT silently resurrect a prior 15-minute trust
    grant. The durable record is the audit log.
    """

    prompter: ConsentPrompter = field(default_factory=FailClosedPrompter)
    clock: Clock = field(default_factory=WallClock)
    audit_logger: object | None = None  # AuditLogger-shaped (.append); typed loosely to avoid import cycle
    tiers: dict[str, TrustTier] = field(default_factory=dict)
    always_ask_tools: frozenset[str] = _DEFAULT_ALWAYS_ASK_TOOLS
    always_ask_categories: frozenset[str] = _DEFAULT_ALWAYS_ASK_CATEGORIES
    window_seconds: float = _DEFAULT_WINDOW_SECONDS

    # ephemeral grant state
    _session_batch: dict[str, set[str]] = field(default_factory=dict, init=False)
    _session_deny: dict[str, set[str]] = field(default_factory=dict, init=False)
    _windows: dict[tuple[str, str], float] = field(default_factory=dict, init=False)

    async def request(
        self,
        *,
        tool_name: str,
        channel: str,
        session_id: str,
        category: str | None = None,
        summary: str = "",
        reversible: bool = False,
    ) -> bool:
        """Return True if the action may proceed. Audits every decision.

        ``reversible`` (F-27): when True the gated effect is low-blast-radius and
        rollback-able (locally owned — derived from the TRUSTED manifest, never
        from LLM-supplied args). A reversible consequential action is auto-allowed
        WITH-UNDO instead of prompting every time, EXCEPT when the tool/category is
        on the always-ask exclusion list (lock/alarm/destructive, execute_code,
        …) — those are never relaxed. Defaults False ⇒ byte-identical to the
        historical prompt-every-time behavior (when in doubt, ask).
        """
        # 1. ENTRY
        log.tool.debug(
            "[consent] policy.request: entry",
            extra={"_fields": {
                "tool": tool_name, "channel": channel, "session": session_id,
                "category": category, "reversible": reversible,
            }},
        )
        tier = self.tiers.get(tool_name, TrustTier.ALWAYS_ASK)
        excluded = self._is_always_ask(tool_name, category)

        # 2. DECISION — hard tiers and standing grants short-circuit the prompt.
        if tier is TrustTier.NEVER:
            return self._finalize(False, tool_name, channel, session_id, category, "tier_never", None)

        if session_id and tool_name in self._session_deny.get(session_id, set()):
            return self._finalize(False, tool_name, channel, session_id, category, "session_deny", None)

        if not excluded:
            if tier is TrustTier.AUTO:
                return self._finalize(True, tool_name, channel, session_id, category, "tier_auto", None)
            # F-27 — a low-blast-radius REVERSIBLE effect (locally owned + undo-able)
            # is auto-allowed-with-undo rather than re-prompted. Only reaches here for
            # NON-excluded tools, so dangerous categories stay on ALWAYS_ASK.
            # ADR-3: route the reversible→auto-allow DECISION through the one
            # ReversibilityResolver. The effect's undo handle is ``undo_write`` (locally
            # owned rollback); a reversible, low-stakes consent does not reach the user
            # (``must_reach_user`` False) ⇒ auto-allow, byte-identical to the inline
            # ``if reversible`` below. OFF ⇒ the inline check runs.
            if reversible:
                if reversibility_resolver_enabled():
                    if not ReversibilityResolver.must_reach_user(
                        Decision(reversibility=Reversibility.reversible(via="undo_write"))
                    ):
                        return self._finalize(
                            True, tool_name, channel, session_id, category,
                            "reversible_auto", None,
                        )
                else:
                    return self._finalize(True, tool_name, channel, session_id, category, "reversible_auto", None)
            if self._window_active(session_id, tool_name):
                return self._finalize(True, tool_name, channel, session_id, category, "window_grant", None)
            if tool_name in self._session_batch.get(session_id, set()):
                return self._finalize(True, tool_name, channel, session_id, category, "session_batch", None)

        # 3. STEP — must ask the user (fail closed if the prompter errors).
        req = ConsentRequest(
            tool_name=tool_name,
            channel=channel,
            session_id=session_id,
            category=category,
            summary=summary,
            allow_relaxation=not excluded,
            reversible=reversible,
        )
        try:
            scope = await self.prompter.prompt(req)
        except Exception as exc:  # fail closed — never allow on a prompt error
            log.tool.error(
                "[consent] policy.request: prompter raised — denying",
                exc_info=exc,
                extra={"_fields": {"tool": tool_name, "channel": channel}},
            )
            return self._finalize(False, tool_name, channel, session_id, category, "prompt_error", None)

        if scope is ConsentScope.DENY:
            return self._finalize(False, tool_name, channel, session_id, category, "user_denied", scope)

        if scope is ConsentScope.DENY_SESSION:
            if session_id:
                self._session_deny.setdefault(session_id, set()).add(tool_name)
                log.tool.debug(
                    "[consent] policy.request: recorded session-deny grant",
                    extra={"_fields": {"tool": tool_name, "session": session_id}},
                )
            return self._finalize(False, tool_name, channel, session_id, category, "user_deny_session", scope)

        # Record any standing grant — but NEVER for excluded tools/categories,
        # and never under an empty session_id (which would collapse every
        # anonymous caller into one shared grant bucket).
        if not excluded and session_id:
            if scope is ConsentScope.SESSION or tier is TrustTier.ASK_ONCE_SESSION:
                self._session_batch.setdefault(session_id, set()).add(tool_name)
                log.tool.debug(
                    "[consent] policy.request: recorded session-batch grant",
                    extra={"_fields": {"tool": tool_name, "session": session_id}},
                )
            elif scope is ConsentScope.WINDOW:
                self._prune_expired_windows()
                self._windows[(session_id, tool_name)] = self.clock.monotonic() + self.window_seconds
                log.tool.debug(
                    "[consent] policy.request: recorded time-window grant",
                    extra={"_fields": {"tool": tool_name, "session": session_id, "window_s": self.window_seconds}},
                )

        return self._finalize(True, tool_name, channel, session_id, category, f"user_{scope.value}", scope)

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _is_always_ask(self, tool_name: str, category: str | None) -> bool:
        return tool_name in self.always_ask_tools or (
            category is not None and category in self.always_ask_categories
        )

    def _prune_expired_windows(self) -> None:
        """Drop expired time-window grants so abandoned entries don't accumulate."""
        now = self.clock.monotonic()
        expired = [key for key, expiry in self._windows.items() if expiry <= now]
        for key in expired:
            del self._windows[key]

    def _window_active(self, session_id: str, tool_name: str) -> bool:
        key = (session_id, tool_name)
        expiry = self._windows.get(key)
        if expiry is None:
            return False
        if self.clock.monotonic() < expiry:
            return True
        # expired — prune so it does not linger
        del self._windows[key]
        return False

    def _finalize(
        self,
        allowed: bool,
        tool_name: str,
        channel: str,
        session_id: str,
        category: str | None,
        reason: str,
        scope: ConsentScope | None,
    ) -> bool:
        decision = "allow" if allowed else "deny"
        # 4. EXIT (audit + log every decision)
        if self.audit_logger is not None:
            try:
                self.audit_logger.append(  # type: ignore[attr-defined]
                    "consent.decision",
                    actor=session_id or "unknown",
                    target=tool_name,
                    details={
                        "channel": channel,
                        "category": category,
                        "decision": decision,
                        "reason": reason,
                        "scope": scope.value if scope is not None else None,
                    },
                )
            except Exception as exc:  # audit must never block the decision
                log.tool.error(
                    "[consent] policy.request: audit append failed",
                    exc_info=exc,
                    extra={"_fields": {"tool": tool_name}},
                )
        log.tool.info(
            "[consent] policy.request: exit",
            extra={"_fields": {"tool": tool_name, "decision": decision, "reason": reason}},
        )
        return allowed
