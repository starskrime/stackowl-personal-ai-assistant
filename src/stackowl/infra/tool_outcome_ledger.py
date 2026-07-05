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
    # The reality check (mirrors ToolResult.verified). None ⇒ not checked (the
    # outcome is judged on `success` alone — byte-identical to pre-verification).
    # False ⇒ the tool claimed success but the effect was NOT observed: an effectful
    # `verified=False` is an UNACHIEVED outcome even though `success` is True.
    verified: bool | None = None
    # ADR-T2 / TS3 — the tool's declared durable-effect class (mirrors
    # ToolManifest.effect_class): "creates_persistent_entity" | "sends_message" |
    # "schedules". None ⇒ read-only / no durable effect (the default). The
    # ledger-driven overclaim veto demands a MEASURED verified==True receipt before a
    # success of an effect-classed tool may stand — an effect-classed outcome whose
    # verified is NOT True (False OR unknown) is an unproven effect (default-deny).
    effect_class: str | None = None
    # The tool's own ToolResult.error text (mirrors ToolResult.error). None when
    # the tool succeeded or reported no error string. Lets the honest give-up
    # floor cite the REAL technical detail instead of a blank slot.
    error: str | None = None


def is_effectful_failure(
    action_severity: str,
    success: bool,
    side_effect_committed: bool = True,
    verified: bool | None = None,
) -> bool:
    """True iff this outcome is a write/consequential effect that did NOT land —
    either it reported failure, OR it claimed success but reality refuted it
    (``verified is False``) — and it crossed (or may have crossed) the side-effect
    boundary.

    THE single source of truth for "did the user's effect fail to land?" — shared by
    the ledger tally, the execute snapshot, and the give-up floor so the three never
    drift. A validation-refused no-op (``side_effect_committed=False``) is excluded:
    nothing was attempted, so there is nothing to be honest about. A ``verified=None``
    outcome falls back to the ``success`` signal (byte-identical to today).
    """
    if action_severity not in _EFFECTFUL or not side_effect_committed:
        return False
    # An unverified claim (success=True, verified=False) is an unachieved effect.
    return not success or verified is False


_outcomes: ContextVar[tuple[ToolOutcome, ...] | None] = ContextVar(
    "tool_outcomes", default=None,
)


def bind() -> Token[tuple[ToolOutcome, ...] | None]:
    return _outcomes.set(())


def reset(token: Token[tuple[ToolOutcome, ...] | None]) -> None:
    _outcomes.reset(token)


def record_tool_outcome(
    *, name: str, action_severity: str, success: bool, side_effect_committed: bool = True,
    verified: bool | None = None, effect_class: str | None = None, error: str | None = None,
) -> None:
    """Record one dispatched tool's outcome. No-op (logged) when unbound; never raises.

    ``side_effect_committed`` defaults True (conservative). Callers pass False for a
    pre-execution refusal (bad/missing args, unavailable store) so it is excluded from
    the unachieved-consequential tally — see :func:`is_effectful_failure`. ``verified``
    mirrors ToolResult.verified (None ⇒ not checked, byte-identical; False ⇒ claimed
    but unobserved → an effectful failure). ``effect_class`` mirrors
    ToolManifest.effect_class (None ⇒ read-only) so the overclaim veto can default-deny
    an effect-classed tool whose effect was not MEASURED verified==True. ``error``
    mirrors ToolResult.error (None ⇒ success or no error string) so the honest
    give-up floor can cite the real technical detail instead of a blank slot.
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
            side_effect_committed=side_effect_committed, verified=verified,
            effect_class=effect_class, error=error,
        ),
    ))


def get_outcomes() -> tuple[ToolOutcome, ...]:
    """Non-consuming read of this turn's recorded outcomes (empty if none/unbound)."""
    return _outcomes.get() or ()


def consequential_tally() -> tuple[int, int]:
    """Return (consequential_failures, consequential_successes) over write+consequential outcomes.

    Verification-aware: a ``verified=False`` effect counts as a FAILURE (not a
    success) even though it self-reported ``success=True`` — a claimed-but-unobserved
    write is an unachieved outcome.
    """
    outcomes = get_outcomes()
    cons_f = sum(
        1 for o in outcomes
        if is_effectful_failure(o.action_severity, o.success, o.side_effect_committed, o.verified)
    )
    cons_s = sum(
        1 for o in outcomes
        if o.action_severity in _EFFECTFUL and o.success and o.verified is not False
    )
    return cons_f, cons_s
