"""BudgetGovernor: the time cap excludes human-wait seconds (Phase 1A)."""

from __future__ import annotations

from stackowl.authz.bounds import ResourceCaps
from stackowl.pipeline.budget.governor import BudgetGovernor


class _FakeClock:
    """Monotonic clock returning a settable 'now' (seconds)."""

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now


def _gov(clock: _FakeClock, *, human_wait: float | None) -> BudgetGovernor:
    src = (lambda: human_wait) if human_wait is not None else None
    return BudgetGovernor(
        ResourceCaps(max_time_s=120.0),
        cost_tracker=None,
        trace_id="t",
        started_monotonic=0.0,
        clock=clock,
        human_wait_source=src,
    )


def test_human_wait_subtracted_from_time_cap() -> None:
    clock = _FakeClock()
    gov = _gov(clock, human_wait=50.0)
    # 130s wall elapsed, but 50s of it was human-wait → effective 80s < 120s cap.
    clock.now = 130.0
    assert gov.check(iteration=2) is None


def test_breach_fires_on_effective_compute_time() -> None:
    clock = _FakeClock()
    gov = _gov(clock, human_wait=50.0)
    # 175s wall − 50s human-wait = 125s effective ≥ 120s → breach, reporting effective.
    clock.now = 175.0
    breach = gov.check(iteration=2)
    assert breach is not None
    assert breach.cap == "time"
    assert breach.actual == 125.0


def test_no_source_is_byte_identical_legacy() -> None:
    clock = _FakeClock()
    gov = _gov(clock, human_wait=None)
    clock.now = 119.0
    assert gov.check(iteration=0) is None
    clock.now = 120.0
    breach = gov.check(iteration=0)
    assert breach is not None and breach.cap == "time" and breach.actual == 120.0


def test_remaining_seconds_subtracts_human_wait() -> None:
    clock = _FakeClock()
    gov = _gov(clock, human_wait=30.0)
    clock.now = 100.0  # effective elapsed = 70s → remaining 50s
    assert gov.remaining_seconds() == 50.0


def test_effective_elapsed_floors_at_zero() -> None:
    clock = _FakeClock()
    gov = _gov(clock, human_wait=200.0)  # over-counts beyond wall time
    clock.now = 100.0
    # effective elapsed floored at 0 → no breach, full budget remains.
    assert gov.check(iteration=0) is None
    assert gov.remaining_seconds() == 120.0
