"""Stopping the scheduler must stop it ACCEPTING, not abandon what is running.

MEASURED 2026-09-08 across the retained window: 26 `ValueError: Connection closed`
errors in TWELVE different components — `[cadence] could not read a store's clock`
(6), `task_liveness_sweep: owner discovery failed` (4), `incident_escalation`
detection and recurrence (6), `owned_repo.fetch_owned`, `owl_rating_contributor`,
`_poll_cycle`, `objective_driver`, `capability_failure_rates`. One cause: the DB
pool closed underneath work that was still running.

THE RESTART PATH IS ASYMMETRIC, and the orchestrator says so in its own comments.
For TURNS it stops accepting and then drains::

    # Stop accepting new turns (the frame loop), then drain the
    # turns already running.
    loop_task.cancel()

For SCHEDULER JOBS it only PROBES::

    await quiesce(turn_registry, background_in_flight=count_running_jobs(...))

`drain.py`'s module docstring states the precondition that makes quiesce sound:
*"The caller stops accepting new turns first (so the running set can only
shrink)"*. That holds for turns. The background probe was added later — its
comment records why, "85 of 462 staged RCAs died that way" — and the STOP half of
the contract was never added with it. So the background running set can GROW
after the check, which is precisely what the invariant forbids.

CAUGHT IN THE ACT, 2026-09-08::

    05:30:22.140  quiesce: nothing in flight — draining clean
    05:30:24.335  scheduler DISPATCHED incident_escalation      <- 2.2s later
    05:30:24.540  ERROR incident_escalation: recurrence check raised
    05:30:24.554  core: exec-replacing with fresh code

The drain answered a question and never closed the door: a check-then-act race by
construction. (509 of 517 exec-replaces DO quiesce, so the drain itself runs — the
defect is not a missing drain, it is a drain that cannot hold.)

WHY THE FIX IS A STOP FLAG AND NOT `scheduler_task.cancel()`. `run()` holds the
ONLY strong references to its in-flight `_poll_cycle` tasks, in a local `inflight`
set — deliberately, because asyncio does not keep strong references to scheduled
tasks and a fire-and-forget task can be garbage-collected mid-execution.
Cancelling `run()` drops that set, so the cure would risk abandoning exactly the
work it is meant to protect. Stopping ACCEPTANCE and then awaiting `inflight`
keeps the references and lets the running cycles finish, which is what the turns
path already does.
"""

from __future__ import annotations

import asyncio
import contextlib

import pytest

from stackowl.scheduler.scheduler import JobScheduler

pytestmark = pytest.mark.asyncio


class _Clock:
    """A GATED clock: the loop parks in `async_sleep` until the test releases it.

    The first version slept for zero, and both tests below failed against correct
    code — the loop simply dispatched a second cycle in the microseconds before
    `stop_accepting()` was called. That is a race in the FIXTURE manufacturing a
    false failure, the mirror of a fixture that cannot show a real one. Gating the
    clock makes the loop park at a known point, so "dispatched after the stop"
    means what it says instead of meaning "the test was slow".
    """

    def __init__(self) -> None:
        self.parked = asyncio.Event()
        self.release = asyncio.Event()
        self.sleeps = 0

    async def async_sleep(self, _seconds: float) -> None:
        self.sleeps += 1
        self.parked.set()
        await self.release.wait()
        self.release.clear()
        self.parked.clear()

    def monotonic(self) -> float:
        return 0.0


def _bare_scheduler() -> JobScheduler:
    """A JobScheduler with only what `run()` touches.

    `__new__` rather than the real constructor on purpose: this exercises the
    REAL `run()` method against a double that supplies exactly its two
    collaborators, so the test cannot pass because a fixture happened to make the
    loop exit early.
    """
    s = JobScheduler.__new__(JobScheduler)
    s._clock = _Clock()  # type: ignore[attr-defined]  # noqa: SLF001
    return s


async def test_stopping_lets_an_in_flight_cycle_finish(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """THE 26 ERRORS. A cycle already dispatched must complete, not be dropped."""
    sched = _bare_scheduler()
    started = asyncio.Event()
    release = asyncio.Event()
    finished: list[str] = []

    async def _slow_cycle() -> None:
        started.set()
        await release.wait()
        finished.append("done")

    monkeypatch.setattr(sched, "_poll_cycle", _slow_cycle, raising=False)

    task = asyncio.create_task(sched.run())
    await asyncio.wait_for(started.wait(), timeout=2)
    # The loop has dispatched exactly one cycle and is parked in the clock.
    await asyncio.wait_for(sched._clock.parked.wait(), timeout=2)  # noqa: SLF001

    sched.stop_accepting()
    sched._clock.release.set()  # noqa: SLF001 — let the loop wake and re-check

    # RUN() MUST NOT RETURN YET. This ordering is the whole test: the first
    # version released the cycle BEFORE checking, so it finished either way and
    # the assertion held even with the drain deleted — proven by mutation, which
    # is the only reason the vacuity was visible.
    for _ in range(20):
        await asyncio.sleep(0)
    assert not task.done(), (
        "run() returned while a dispatched cycle was still running — the pool "
        "can now close underneath it, which is the 26-error defect"
    )
    assert finished == []

    release.set()               # now let the in-flight cycle complete
    await asyncio.wait_for(task, timeout=5)
    assert finished == ["done"]


async def test_stopping_starts_no_further_cycles(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """THE RACE. After the door closes, nothing new may be dispatched — that is
    the whole property `quiesce` depends on and could not previously rely on."""
    sched = _bare_scheduler()
    dispatched = 0

    async def _count_cycle() -> None:
        nonlocal dispatched
        dispatched += 1

    monkeypatch.setattr(sched, "_poll_cycle", _count_cycle, raising=False)

    task = asyncio.create_task(sched.run())
    await asyncio.wait_for(sched._clock.parked.wait(), timeout=2)  # noqa: SLF001
    seen = dispatched            # exactly what was dispatched before the stop

    sched.stop_accepting()
    sched._clock.release.set()   # noqa: SLF001 — the loop wakes and must NOT dispatch
    await asyncio.wait_for(task, timeout=5)
    for _ in range(20):          # give the loop every chance to dispatch again
        await asyncio.sleep(0)

    assert dispatched == seen, (
        f"{dispatched - seen} cycle(s) were dispatched AFTER the scheduler was "
        f"told to stop; the running set can still grow, so a drain that measured "
        f"it a moment earlier proves nothing"
    )


async def test_run_returns_rather_than_looping_forever_once_stopped(
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    """`run()` must RETURN, or the orchestrator's `await` after stopping would
    hang the restart it is trying to make safe."""
    sched = _bare_scheduler()

    async def _noop_cycle() -> None:
        return None

    monkeypatch.setattr(sched, "_poll_cycle", _noop_cycle, raising=False)

    task = asyncio.create_task(sched.run())
    await asyncio.wait_for(sched._clock.parked.wait(), timeout=2)  # noqa: SLF001
    sched.stop_accepting()
    sched._clock.release.set()  # noqa: SLF001

    await asyncio.wait_for(task, timeout=5)
    assert task.done() and not task.cancelled()


async def test_the_stop_is_sticky_against_the_supervisors_restart(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """THE SUPERVISOR WOULD OTHERWISE RE-OPEN THE DOOR.

    The scheduler runs under `Supervisor`, whose `_run_with_backoff` RESTARTS a
    task that returns cleanly. A non-sticky stop would therefore reopen dispatch
    within one backoff — during exactly the seconds the restart is trying to keep
    quiet — and the drain would be racing again.

    It must also not return INSTANTLY on that re-entry:
    `Supervisor._record_clean_return` treats a run shorter than
    `tight_loop_seconds` as a spin and escalates after a few in a row, so an
    eager return would report a shutdown as a fault. It parks instead.
    """
    sched = _bare_scheduler()
    dispatched = 0

    async def _count_cycle() -> None:
        nonlocal dispatched
        dispatched += 1

    monkeypatch.setattr(sched, "_poll_cycle", _count_cycle, raising=False)

    first = asyncio.create_task(sched.run())
    await asyncio.wait_for(sched._clock.parked.wait(), timeout=2)  # noqa: SLF001
    sched.stop_accepting()
    sched._clock.release.set()  # noqa: SLF001
    await asyncio.wait_for(first, timeout=5)
    after_stop = dispatched

    # The supervisor restarts it. It must neither dispatch nor return.
    second = asyncio.create_task(sched.run())
    for _ in range(50):
        await asyncio.sleep(0)

    assert dispatched == after_stop, (
        "a supervisor restart re-opened dispatch after the scheduler was stopped"
    )
    assert not second.done(), (
        "run() returned instantly on re-entry; the supervisor's tight-loop guard "
        "would escalate this shutdown as a spinning task"
    )
    second.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await second
