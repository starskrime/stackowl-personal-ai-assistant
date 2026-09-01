"""The scheduler defers to turns. The drain waited for turns. Nothing waited for
the scheduler.

MEASURED 2026-09-01, two symptoms of one cause:

* ``[rca] staged.analyze`` entered 462 times and exited 355, and **85 of the 107
  lost analyses had a RESTART as their next event** — each having spent up to
  ~140,000 tokens producing no verdict;
* **48 "Cannot operate on a closed database" errors, 100% of them within 120
  seconds of a boot** (52% within 30s, none within 5s) — and ``grace_seconds`` is
  **120.0**. The correlation window IS the drain window.

Per-day counts track boots, not load: 8/47/69/91 boots against 2/4/12/26 events.

THE ASYMMETRY IS THE ROOT CAUSE. ``scheduler.py`` already defers to turns — it
logs "deferred — user turn active (heavy job yields)" — and ``quiesce`` drained
turns. So background work yielded in BOTH directions and nothing ever waited for
it. Worse, ``quiesce``'s first line was a fast path on ``has_active_turns()``,
and an AUTOMATIC restart is by definition the case where no user is attached: it
returned "draining clean" immediately and exec-replaced whatever the scheduler
had in flight.

ONE SOURCE, NOT A SECOND TRACKER. A claimed job row is ``status='running'`` under
the scheduler's own CAS claim — the same vocabulary ``reap_stale_running`` reaps.
``count_running_jobs`` asks that; it does not invent a registry.

THE CEILING IS DELIBERATELY UNCHANGED. A staged RCA budgets up to 960s, eight
times the 120s grace, so a long one is still abandoned — that tradeoff (deploy
speed against work loss) is the operator's and is ESC-101. What this removes is
the common case: ``incident_escalation`` has a p50 of 0.3s and a p90 of 13s, so
almost every handler now finishes instead of being killed.

IT FAILS TOWARD RESTARTING, everywhere. An absent probe, a raising probe or an
unreadable table all report zero in flight, because a probe that cannot answer
must never be able to hold a deploy open — that would be a worse failure than the
one this prevents.
"""

from __future__ import annotations

import pytest

from stackowl.runtime.drain import quiesce

pytestmark = pytest.mark.asyncio


class _Turns:
    def __init__(self, active: int = 0) -> None:
        self.active = active

    def has_active_turns(self) -> bool:
        return self.active > 0

    def active_turn_count(self) -> int:
        return self.active


def _probe(counts: list[int]):  # noqa: ANN202
    """A probe that walks `counts`, holding the last value forever."""

    async def _p() -> int:
        return counts.pop(0) if len(counts) > 1 else counts[0]

    return _p


async def test_a_restart_with_no_user_waits_for_background_work() -> None:
    """The exact hole: an automatic restart has no active turn, so the fast path
    fired immediately and killed whatever the scheduler was running."""
    drained = await quiesce(
        _Turns(active=0), grace_seconds=5.0, poll_interval_s=0.01,
        background_in_flight=_probe([1, 1, 0]),
    )
    assert drained is True


async def test_it_still_drains_clean_when_nothing_is_running() -> None:
    """The fast path must survive — a quiet platform restarts instantly."""
    assert await quiesce(
        _Turns(active=0), grace_seconds=5.0, poll_interval_s=0.01,
        background_in_flight=_probe([0]),
    ) is True


async def test_a_straggling_job_is_abandoned_at_the_ceiling() -> None:
    """Bounded by construction. A stuck 'running' row must not hold a deploy open
    for ever — the grace ceiling is what makes waiting safe at all."""
    drained = await quiesce(
        _Turns(active=0), grace_seconds=0.05, poll_interval_s=0.01,
        background_in_flight=_probe([3]),
    )
    assert drained is False


async def test_turns_still_drain_as_before() -> None:
    """The behaviour this had before must not regress."""
    turns = _Turns(active=1)

    async def _clear() -> int:
        turns.active = 0
        return 0

    assert await quiesce(
        turns, grace_seconds=5.0, poll_interval_s=0.01, background_in_flight=_clear,
    ) is True


async def test_no_probe_behaves_exactly_as_before() -> None:
    """Back-compat: an unwired caller gets the old semantics, byte for byte."""
    assert await quiesce(
        _Turns(active=0), grace_seconds=5.0, poll_interval_s=0.01,
    ) is True


async def test_a_raising_probe_never_holds_the_deploy_open() -> None:
    """Fails toward restarting. A probe that cannot answer must not be able to
    block a restart — worse than the failure it exists to prevent."""

    async def _boom() -> int:
        raise RuntimeError("db is gone")

    assert await quiesce(
        _Turns(active=0), grace_seconds=5.0, poll_interval_s=0.01,
        background_in_flight=_boom,
    ) is True


async def test_a_negative_count_is_treated_as_idle() -> None:
    """Defensive: a nonsense count must not loop forever."""

    async def _weird() -> int:
        return -3

    assert await quiesce(
        _Turns(active=0), grace_seconds=5.0, poll_interval_s=0.01,
        background_in_flight=_weird,
    ) is True


def test_the_probe_asks_the_scheduler_s_own_vocabulary() -> None:
    """Structural: a second definition of "in flight" would drift from the one
    the reaper already uses, and the two would disagree exactly when it matters."""
    import inspect

    from stackowl.scheduler import scheduler_helpers

    source = inspect.getsource(scheduler_helpers.count_running_jobs)
    assert "status = 'running'" in source
    reaper = inspect.getsource(scheduler_helpers.reap_stale_running)
    assert "status = 'running'" in reaper, (
        "the reaper's definition of a claimed job moved — the drain probe now "
        "asks a different question than the thing that cleans up after it"
    )


def test_the_probe_is_wired_at_the_restart_seam() -> None:
    """A feature ships ON. Unwired, this is the exact bug it fixes: a drain that
    cannot see background work."""
    import inspect

    from stackowl.startup import orchestrator

    source = inspect.getsource(orchestrator)
    assert "background_in_flight=" in source, (
        "quiesce is called without the background probe — automatic restarts are "
        "killing in-flight scheduler work again"
    )
    assert "count_running_jobs" in source
