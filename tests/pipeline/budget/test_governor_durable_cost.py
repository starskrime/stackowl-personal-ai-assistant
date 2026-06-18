"""PROV-3 (F093) — the cost ceiling is CUMULATIVE across a durable resume.

The in-memory cost ledger resets to 0 each attempt, so a parked+resumed task
used to restart cost accounting from $0 every attempt and could spend
``max_cost_usd`` PER attempt without bound. The governor must be seedable with
the prior accumulated spend so the ceiling holds across the whole task lifetime.
"""

from __future__ import annotations

from stackowl.authz.bounds import ResourceCaps
from stackowl.pipeline.budget.governor import BudgetGovernor


class _Clock:
    def monotonic(self) -> float:
        return 0.0


class _CostStub:
    def __init__(self, usd: float) -> None:
        self.usd = usd

    def turn_cost_usd(self, trace_id: str) -> float:
        return self.usd


def _gov(caps: ResourceCaps, *, this_attempt: float, prior: float) -> BudgetGovernor:
    return BudgetGovernor(
        caps,
        cost_tracker=_CostStub(this_attempt),
        trace_id="t",
        started_monotonic=0.0,
        clock=_Clock(),
        prior_cost_usd=prior,
    )


def test_prior_spend_plus_attempt_trips_cumulative_ceiling() -> None:
    # Cap = 1.0. Prior attempts already spent 0.8; this attempt has spent 0.3.
    # Cumulative 1.1 >= 1.0 must trip even though THIS attempt alone (0.3) is under.
    g = _gov(ResourceCaps(max_cost_usd=1.0), this_attempt=0.3, prior=0.8)
    breach = g.check(0)
    assert breach is not None and breach.cap == "cost"
    assert abs(breach.actual - 1.1) < 1e-9


def test_under_cumulative_does_not_trip() -> None:
    g = _gov(ResourceCaps(max_cost_usd=1.0), this_attempt=0.1, prior=0.5)
    assert g.check(0) is None


def test_no_prior_seed_is_backward_compatible() -> None:
    # Default prior=0.0 → same as the legacy per-attempt behavior.
    g = BudgetGovernor(
        ResourceCaps(max_cost_usd=1.0),
        cost_tracker=_CostStub(1.5),
        trace_id="t",
        started_monotonic=0.0,
        clock=_Clock(),
    )
    breach = g.check(0)
    assert breach is not None and breach.cap == "cost" and abs(breach.actual - 1.5) < 1e-9
