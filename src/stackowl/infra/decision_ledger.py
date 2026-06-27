"""DecisionLedger (ADR-7) — turn-scoped, queryable record of every authority's verdict.

The 4-point logs + ``traceId``/``withSpan`` capture *execution* (what ran, how long).
They do NOT capture *decisions* — what was decided, why, and which alternative it beat.
Those verdicts are exactly the ones the ADR-1–6 authorities already produce
(AcceptanceAuthority, RecoveryActuator, ReversibilityResolver, LearnedContext, the
router/classifier). ADR-7 is a *consumption* layer: each authority emits one typed
:class:`Decision` to this per-turn ledger, and "explain what you did and why" becomes a
*read* of the ledger, not a reconstruction.

Mirrors the ``recovery_context`` / ``tool_outcome_ledger`` ContextVar idiom exactly:
lives in ``infra/`` (the base layer) so any layer can record WITHOUT a dependency
inversion. The backend ``bind()``s a fresh ledger at turn start and ``reset()``s it in a
``finally``; emit sites call :func:`record_decision`; consumers (recovery_summary, the
crash path, an /explain surface) read via the NON-consuming :func:`get_decisions`.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass, field

from stackowl.infra.observability import log


@dataclass(frozen=True)
class Decision:
    """One consequential decision a turn made, with its verdict and the reason.

    ``point`` — stable identifier of the decider (e.g. ``"acceptance"``, ``"recovery"``,
    ``"reversibility"``, ``"learned_context"``, ``"router"``, ``"crash"``, ``"next_step"``).
    ``verdict`` — the outcome in the decider's own terms (e.g. ``"accepted"``,
    ``"surrendered"``, ``"act"``, ``"ask"``, a classified intent). ``inputs`` /
    ``alternatives_considered`` / ``evidence`` are free-form context maps — what fed the
    decision, what it beat, and the observation backing it. All log-safe by construction
    (emit sites pass names/verdicts, never secrets — same discipline as the 4-point logs).
    """

    point: str
    verdict: str
    reason: str = ""
    inputs: dict[str, object] = field(default_factory=dict)
    alternatives_considered: tuple[str, ...] = ()
    evidence: dict[str, object] = field(default_factory=dict)


_decisions: ContextVar[tuple[Decision, ...] | None] = ContextVar(
    "decisions", default=None,
)


def bind() -> Token[tuple[Decision, ...] | None]:
    """Install a fresh empty ledger for one turn. Returns a reset token."""
    return _decisions.set(())


def reset(token: Token[tuple[Decision, ...] | None]) -> None:
    """Restore the prior ledger (call in a ``finally``)."""
    _decisions.reset(token)


def record_decision(
    *,
    point: str,
    verdict: str,
    reason: str = "",
    inputs: dict[str, object] | None = None,
    alternatives_considered: tuple[str, ...] = (),
    evidence: dict[str, object] | None = None,
) -> None:
    """Record one decision. No-op (logged) when unbound; never raises.

    Unbound ⇒ the ledger flag is off (the backend skipped ``bind()``) or this runs
    outside a turn — either way recording is a silent no-op, so emit sites need no flag
    check of their own (byte-identical when the ledger is off)."""
    current = _decisions.get()
    if current is None:
        log.engine.debug(
            "[decision_ledger] record_decision: unbound turn — ignoring",
            extra={"_fields": {"point": point, "verdict": verdict}},
        )
        return
    _decisions.set((*current, Decision(
        point=point, verdict=verdict, reason=reason,
        inputs=inputs or {}, alternatives_considered=alternatives_considered,
        evidence=evidence or {},
    )))


def get_decisions() -> tuple[Decision, ...]:
    """Non-consuming read of this turn's decisions (empty if none/unbound)."""
    return _decisions.get() or ()
