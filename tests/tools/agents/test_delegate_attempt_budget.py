"""CONC-6 (F158) — the delegation attempt-budget counter must not be wiped for
LIVE in-flight turns.

Before the fix ``_charge_attempt`` did ``self._attempts.clear()`` once the dict
crossed 256 entries — nuking EVERY trace's counter, including active turns, so a
fork-bomb rail (MAX_DELEGATION_ATTEMPTS_PER_TURN) silently reset mid-turn.

The fix evicts a trace's attempt counter on turn completion (when its in-flight
count returns to zero in ``_release``) and, as a bounded safety net, evicts the
OLDEST IDLE entry — never a live one. The high-traffic test below drives many
completed traces past the bound while ONE trace stays live, and asserts the live
trace's rail stays monotonic (its budget is never reset under it).
"""

from __future__ import annotations

import threading

from stackowl.owls.delegation_limits import MAX_DELEGATION_ATTEMPTS_PER_TURN
from stackowl.tools.agents.delegate_task import DelegateTaskTool


def test_live_trace_budget_not_reset_by_high_traffic() -> None:
    tool = DelegateTaskTool()

    live = "live-trace"
    # The live turn is in-flight (acquired a width slot) and has charged some
    # attempts — its rail must keep counting up monotonically.
    assert tool._try_acquire(live) is True
    for _ in range(3):
        assert tool._charge_attempt(live) is True

    # Churn a large number of OTHER traces through the full lifecycle
    # (acquire -> charge -> release), far exceeding any internal bound.
    for i in range(2000):
        other = f"other-{i}"
        tool._try_acquire(other)
        tool._charge_attempt(other)
        tool._release(other)

    # The live trace has charged 3; the next charges must continue from 3, never
    # restart at 0. So exactly (CAP - 3) more must succeed, then refuse.
    remaining = MAX_DELEGATION_ATTEMPTS_PER_TURN - 3
    granted = 0
    while tool._charge_attempt(live):
        granted += 1
        if granted > MAX_DELEGATION_ATTEMPTS_PER_TURN * 2:  # runaway guard
            break
    assert granted == remaining, (
        f"live budget was reset: expected {remaining} more grants, got {granted}"
    )


def test_release_to_zero_evicts_attempt_counter() -> None:
    tool = DelegateTaskTool()
    trace = "t1"
    tool._try_acquire(trace)
    tool._charge_attempt(trace)
    # Turn completes -> in-flight returns to zero -> attempt counter evicted so a
    # FUTURE turn reusing the (recycled) trace id starts with a fresh budget.
    tool._release(trace)
    assert trace not in tool._attempts


def test_concurrent_charge_across_traces_is_consistent() -> None:
    """A real race: many threads charge their own traces while the live trace's
    rail must remain its own, uncorrupted by another trace crossing the bound."""
    tool = DelegateTaskTool()
    live = "live"
    tool._try_acquire(live)

    start = threading.Barrier(8)
    errors: list[BaseException] = []

    def worker(wid: int) -> None:
        start.wait()
        try:
            for i in range(500):
                tr = f"w{wid}-{i}"
                tool._try_acquire(tr)
                tool._charge_attempt(tr)
                tool._release(tr)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(w,)) for w in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, f"concurrent charge raised: {errors!r}"
    # The live trace was never charged during the storm — its first charge here
    # must be grant #1 (counter intact / present, never wiped to break monotonicity).
    assert tool._charge_attempt(live) is True
