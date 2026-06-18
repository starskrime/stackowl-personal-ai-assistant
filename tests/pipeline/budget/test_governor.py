"""E2-S4 — BudgetGovernor: cost(best-effort)/steps/time ceilings; in-memory raise."""

from __future__ import annotations

from stackowl.authz.bounds import ResourceCaps
from stackowl.pipeline.budget.governor import BudgetGovernor


class _Clock:
    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def monotonic(self) -> float:
        return self.t


class _CostStub:
    def __init__(self, usd: float) -> None:
        self.usd = usd

    def turn_cost_usd(self, trace_id: str) -> float:
        return self.usd


def _gov(caps: ResourceCaps, *, cost: float = 0.0, clock: _Clock | None = None) -> BudgetGovernor:
    return BudgetGovernor(
        caps, cost_tracker=_CostStub(cost), trace_id="t",
        started_monotonic=0.0, clock=clock or _Clock(),
    )


def test_steps_trips_at_limit_not_before() -> None:
    g = _gov(ResourceCaps(max_steps=2))
    assert g.check(0) is None
    breach = g.check(1)
    assert breach is not None and breach.cap == "steps" and breach.limit == 2


def test_time_trips_on_elapsed() -> None:
    clock = _Clock(0.0)
    g = _gov(ResourceCaps(max_time_s=10.0), clock=clock)
    assert g.check(0) is None
    clock.t = 11.0
    breach = g.check(1)
    assert breach is not None and breach.cap == "time"


def test_cost_trips_when_priced() -> None:
    g = _gov(ResourceCaps(max_cost_usd=1.0), cost=1.5)
    breach = g.check(0)
    assert breach is not None and breach.cap == "cost" and breach.actual == 1.5


def test_zero_cost_never_trips_and_never_disables_steps() -> None:
    g = _gov(ResourceCaps(max_cost_usd=1.0, max_steps=1), cost=0.0)
    breach = g.check(0)
    assert breach is not None and breach.cap == "steps"


def test_all_none_caps_never_trips() -> None:
    g = _gov(ResourceCaps())
    assert g.check(0) is None
    assert g.check(99) is None


def test_first_set_cap_precedence() -> None:
    g = _gov(ResourceCaps(max_steps=1, max_time_s=0.0), clock=_Clock(100.0))
    breach = g.check(0)
    assert breach is not None and breach.cap == "steps"


def test_raise_caps_lifts_the_breached_cap() -> None:
    g = _gov(ResourceCaps(max_steps=1))
    assert g.check(0) is not None
    g.raise_caps("steps")
    assert g.check(1) is None


# F027/SP-4 — remaining_seconds() residual budget accessor for the wrap-up bound.


def test_remaining_seconds_none_when_no_time_cap() -> None:
    g = _gov(ResourceCaps(max_steps=5))  # no time cap
    assert g.remaining_seconds() is None


def test_remaining_seconds_counts_down_with_the_clock() -> None:
    clock = _Clock(0.0)
    g = _gov(ResourceCaps(max_time_s=10.0), clock=clock)
    assert g.remaining_seconds() == 10.0
    clock.t = 4.0
    assert g.remaining_seconds() == 6.0


def test_remaining_seconds_floors_at_zero_never_negative() -> None:
    clock = _Clock(0.0)
    g = _gov(ResourceCaps(max_time_s=10.0), clock=clock)
    clock.t = 25.0  # over budget
    assert g.remaining_seconds() == 0.0
