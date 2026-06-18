"""Turn-scoped ledger of dispatched tool outcomes (name, severity, success).

Mirrors the ``recovery_context`` ContextVar idiom. Lets the give-up detection be
SEVERITY-AWARE: a failed CONSEQUENTIAL/WRITE action with no consequential success
means the user's effect was not achieved — a give-up no matter how confident the
draft. The backend binds a fresh ledger per turn and resets it in a finally.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass

from stackowl.infra.observability import log

_EFFECTFUL = {"write", "consequential"}


@dataclass(frozen=True)
class ToolOutcome:
    name: str
    action_severity: str
    success: bool


_outcomes: ContextVar[tuple[ToolOutcome, ...] | None] = ContextVar(
    "tool_outcomes", default=None,
)


def bind() -> Token[tuple[ToolOutcome, ...] | None]:
    return _outcomes.set(())


def reset(token: Token[tuple[ToolOutcome, ...] | None]) -> None:
    _outcomes.reset(token)


def record_tool_outcome(*, name: str, action_severity: str, success: bool) -> None:
    """Record one dispatched tool's outcome. No-op (logged) when unbound; never raises."""
    current = _outcomes.get()
    if current is None:
        log.engine.debug(
            "[tool_outcome_ledger] record: unbound turn — ignoring",
            extra={"_fields": {"name": name}},
        )
        return
    _outcomes.set((*current, ToolOutcome(name=name, action_severity=action_severity, success=success)))


def get_outcomes() -> tuple[ToolOutcome, ...]:
    """Non-consuming read of this turn's recorded outcomes (empty if none/unbound)."""
    return _outcomes.get() or ()


def consequential_tally() -> tuple[int, int]:
    """Return (consequential_failures, consequential_successes) over write+consequential outcomes."""
    outcomes = get_outcomes()
    cons_f = sum(1 for o in outcomes if o.action_severity in _EFFECTFUL and not o.success)
    cons_s = sum(1 for o in outcomes if o.action_severity in _EFFECTFUL and o.success)
    return cons_f, cons_s
