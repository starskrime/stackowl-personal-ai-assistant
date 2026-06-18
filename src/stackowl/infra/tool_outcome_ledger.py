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
    # Whether the call crossed the side-effect boundary (mirrors
    # ToolResult.side_effect_committed). Default True ⇒ conservative: an undeclared
    # failure is treated as effectful so the honest floor still fires. A False here
    # marks a pre-execution refusal (bad/missing args, unavailable store) that did
    # nothing — it must NOT count as an unachieved consequential outcome.
    side_effect_committed: bool = True


def is_effectful_failure(
    action_severity: str, success: bool, side_effect_committed: bool = True,
) -> bool:
    """True iff this outcome is a write/consequential FAILURE that crossed (or may
    have crossed) the side-effect boundary.

    THE single source of truth for "did the user's effect fail to land?" — shared by
    the ledger tally, the execute snapshot, and the give-up floor so the three never
    drift. A validation-refused no-op (``side_effect_committed=False``) is excluded:
    nothing was attempted, so there is nothing to be honest about.
    """
    return action_severity in _EFFECTFUL and not success and side_effect_committed


_outcomes: ContextVar[tuple[ToolOutcome, ...] | None] = ContextVar(
    "tool_outcomes", default=None,
)


def bind() -> Token[tuple[ToolOutcome, ...] | None]:
    return _outcomes.set(())


def reset(token: Token[tuple[ToolOutcome, ...] | None]) -> None:
    _outcomes.reset(token)


def record_tool_outcome(
    *, name: str, action_severity: str, success: bool, side_effect_committed: bool = True,
) -> None:
    """Record one dispatched tool's outcome. No-op (logged) when unbound; never raises.

    ``side_effect_committed`` defaults True (conservative). Callers pass False for a
    pre-execution refusal (bad/missing args, unavailable store) so it is excluded from
    the unachieved-consequential tally — see :func:`is_effectful_failure`.
    """
    current = _outcomes.get()
    if current is None:
        log.engine.debug(
            "[tool_outcome_ledger] record: unbound turn — ignoring",
            extra={"_fields": {"name": name}},
        )
        return
    _outcomes.set((
        *current,
        ToolOutcome(
            name=name, action_severity=action_severity, success=success,
            side_effect_committed=side_effect_committed,
        ),
    ))


def get_outcomes() -> tuple[ToolOutcome, ...]:
    """Non-consuming read of this turn's recorded outcomes (empty if none/unbound)."""
    return _outcomes.get() or ()


def consequential_tally() -> tuple[int, int]:
    """Return (consequential_failures, consequential_successes) over write+consequential outcomes."""
    outcomes = get_outcomes()
    cons_f = sum(
        1 for o in outcomes
        if is_effectful_failure(o.action_severity, o.success, o.side_effect_committed)
    )
    cons_s = sum(1 for o in outcomes if o.action_severity in _EFFECTFUL and o.success)
    return cons_f, cons_s
