"""The ONE loop — claim, run in parallel, deliver, never stop.

BAKIR, 2026-08-17: *"loop fires every five seconds internally. Checks, process,
continues. Checks, process, continues. Till it achieves the goal."*

WHAT THE LOOP OWNS, and what it deliberately does not. The store (slice 1) owns how
a task MOVES between states — claim, requeue-with-learning, deliver, dead-letter.
This owns only PACING and CONCURRENCY: which rows to offer this tick, how many to
run at once, and making sure a worker's crash cannot take the loop down with it.
Keeping the two apart is why the claim semantics could be proven without a running
event loop, and why this file needs no database assertions.

THE PROPERTY THAT MATTERS MOST is that the loop never dies. A loop that stops on an
unhandled exception is worse than no loop: work accumulates in a table nobody is
draining, and nothing reports it. Every test below that looks defensive is really
about that one property.
"""

from __future__ import annotations

import asyncio

import pytest

from stackowl.pipeline.durable.loop import TaskLoop
from stackowl.pipeline.durable.task import DurableTask

pytestmark = pytest.mark.asyncio


def _task(task_id: str, **over: object) -> DurableTask:
    base: dict = dict(task_id=task_id, goal=f"goal {task_id}", status="pending")
    base.update(over)
    return DurableTask(**base)


class _Store:
    """A store double with the REAL method names and signatures the loop calls.

    Written against the actual DurableTaskStore surface on purpose: a double that
    drifts from the thing it stands in for is how a green suite hides a broken
    production path, which this tree has paid for repeatedly.
    """

    def __init__(self, pending: list[DurableTask] | None = None) -> None:
        self._pending = list(pending or [])
        self.claimed: list[str] = []
        self.delivered: list[tuple[str, str]] = []
        self.failed: list[tuple[str, str, str]] = []
        self.reclaims = 0
        self.prunes = 0
        self.claim_returns = True

    async def claimable(self, *, limit: int = 10, now: object = None) -> list[DurableTask]:
        return self._pending[:limit]

    async def claim(self, task_id: str, *, worker: str, lease_seconds: int = 900) -> bool:
        if not self.claim_returns:
            return False
        self.claimed.append(task_id)
        self._pending = [t for t in self._pending if t.task_id != task_id]
        return True

    async def mark_delivered(self, task_id: str, *, result: str) -> None:
        self.delivered.append((task_id, result))

    async def fail_and_requeue(
        self, task_id: str, *, error: str, failure_class: str = "",
        banned: tuple[str, ...] = (),
    ) -> str:
        self.failed.append((task_id, error, failure_class))
        return "pending"

    async def reclaim_expired(self, *, now: object = None) -> int:
        self.reclaims += 1
        return 0

    async def prune_completed(self, *, older_than_days: int = 1) -> int:
        self.prunes += 1
        return 0


class TestItRunsWorkInParallel:
    async def test_five_pending_rows_run_CONCURRENTLY(self) -> None:
        """Bakir: "if in table we have five pending, five loops parallel... they
        will all do same same time. There's no ordering."

        Proven by overlap, not by counting calls: each task parks on an event that
        is only released once ALL five have started. If the loop were sequential
        this deadlocks and the test times out — which is exactly the failure a
        call-count assertion would miss.
        """
        started = asyncio.Event()
        arrived = 0

        async def _run(task: DurableTask) -> str:
            nonlocal arrived
            arrived += 1
            if arrived == 5:
                started.set()
            await asyncio.wait_for(started.wait(), timeout=5)
            return f"done {task.task_id}"

        store = _Store([_task(f"t{i}") for i in range(5)])
        loop = TaskLoop(store=store, runner=_run, max_parallel=5)  # type: ignore[arg-type]

        await loop.tick()

        assert arrived == 5, "the five tasks did not run at the same time"
        assert len(store.delivered) == 5

    async def test_max_parallel_is_a_real_ceiling(self) -> None:
        """A backlog must not take the whole box. The ceiling bounds how much of
        this hardware a runaway queue can claim."""
        peak = 0
        live = 0

        async def _run(task: DurableTask) -> str:
            nonlocal peak, live
            live += 1
            peak = max(peak, live)
            await asyncio.sleep(0.01)
            live -= 1
            return "ok"

        store = _Store([_task(f"t{i}") for i in range(8)])
        loop = TaskLoop(store=store, runner=_run, max_parallel=3)  # type: ignore[arg-type]

        await loop.tick()

        assert peak <= 3, f"ran {peak} at once against a ceiling of 3"


class TestCompletionMeansDelivery:
    async def test_a_successful_run_marks_the_task_DELIVERED(self) -> None:
        async def _run(task: DurableTask) -> str:
            return "Your name is Friday."

        store = _Store([_task("t1")])
        loop = TaskLoop(store=store, runner=_run, max_parallel=5)  # type: ignore[arg-type]

        await loop.tick()

        assert store.delivered == [("t1", "Your name is Friday.")]

    async def test_a_runner_that_returns_NOTHING_is_a_failure_not_a_success(self) -> None:
        """The achievement condition. A worker that returns an empty result has not
        delivered anything, and marking that complete would be the overclaim this
        platform keeps finding — success asserted rather than observed."""
        async def _run(task: DurableTask) -> str:
            return "   "

        store = _Store([_task("t1")])
        loop = TaskLoop(store=store, runner=_run, max_parallel=5)  # type: ignore[arg-type]

        await loop.tick()

        assert store.delivered == []
        assert store.failed and store.failed[0][0] == "t1"


class TestOneBadTaskCannotStopTheLoop:
    async def test_a_raising_task_is_requeued_with_what_broke(self) -> None:
        async def _run(task: DurableTask) -> str:
            raise RuntimeError("provider unreachable")

        store = _Store([_task("t1")])
        loop = TaskLoop(store=store, runner=_run, max_parallel=5)  # type: ignore[arg-type]

        await loop.tick()

        assert store.failed
        task_id, error, _cls = store.failed[0]
        assert task_id == "t1"
        assert "provider unreachable" in error

    async def test_one_failure_does_not_stop_its_SIBLINGS(self) -> None:
        """asyncio.gather without return_exceptions would let the first raise
        cancel the rest, so one bad row would silently drop four good ones."""
        async def _run(task: DurableTask) -> str:
            if task.task_id == "bad":
                raise RuntimeError("boom")
            return "ok"

        store = _Store([_task("bad"), *[_task(f"t{i}") for i in range(4)]])
        loop = TaskLoop(store=store, runner=_run, max_parallel=5)  # type: ignore[arg-type]

        await loop.tick()

        assert len(store.delivered) == 4, "a sibling's failure took the others down"
        assert len(store.failed) == 1

    async def test_a_failure_is_CLASSIFIED_so_the_ceiling_can_be_smart(self) -> None:
        """A transient blip and a permanent refusal must not spend the same budget.
        The class is what lets the store stop early on the second."""
        async def _run(task: DurableTask) -> str:
            raise ConnectionError("connection reset")

        store = _Store([_task("t1")])
        loop = TaskLoop(store=store, runner=_run, max_parallel=5)  # type: ignore[arg-type]

        await loop.tick()

        assert store.failed[0][2] == "transient", store.failed


class TestTheLoopSurvivesItself:
    async def test_a_store_that_raises_does_not_kill_the_tick(self) -> None:
        """The property everything else rests on. If a tick can die, work piles up
        in a table nobody drains and nothing reports it."""
        class _Broken(_Store):
            async def claimable(self, *, limit: int = 10, now: object = None):
                raise RuntimeError("database gone")

        loop = TaskLoop(store=_Broken(), runner=_never, max_parallel=5)  # type: ignore[arg-type]

        await loop.tick()  # must not raise

    async def test_housekeeping_runs_even_when_there_is_no_work(self) -> None:
        """Reclaiming crashed leases and pruning delivered rows are what the tick is
        FOR when the queue is empty. Skipping them on an idle tick would mean a
        crashed worker's row is only recovered once new work happens to arrive."""
        store = _Store([])
        loop = TaskLoop(store=store, runner=_never, max_parallel=5)  # type: ignore[arg-type]

        await loop.tick()

        assert store.reclaims == 1
        assert store.prunes == 1

    async def test_a_lost_claim_is_not_run(self) -> None:
        """Another worker won the row. Running it anyway is the double-execution the
        CAS claim exists to prevent."""
        ran: list[str] = []

        async def _run(task: DurableTask) -> str:
            ran.append(task.task_id)
            return "ok"

        store = _Store([_task("t1")])
        store.claim_returns = False
        loop = TaskLoop(store=store, runner=_run, max_parallel=5)  # type: ignore[arg-type]

        await loop.tick()

        assert ran == [], "ran a task this worker did not win"


class TestItKeepsTicking:
    async def test_start_ticks_repeatedly_then_stops_cleanly(self) -> None:
        """"loop never ends" — but it must also stop on command, or a restart hangs
        waiting for a task that will never finish."""
        ticks = 0

        async def _run(task: DurableTask) -> str:  # pragma: no cover — no work
            return "ok"

        class _Counting(_Store):
            async def claimable(self, *, limit: int = 10, now: object = None):
                nonlocal ticks
                ticks += 1
                return []

        loop = TaskLoop(store=_Counting(), runner=_run, max_parallel=5,  # type: ignore[arg-type]
                        tick_seconds=0.01)
        await loop.start()
        await asyncio.sleep(0.06)
        await loop.stop()

        assert ticks >= 2, f"the loop did not keep ticking (saw {ticks})"
        after = ticks
        await asyncio.sleep(0.05)
        assert ticks == after, "the loop kept running after stop()"


async def _never(task: DurableTask) -> str:  # pragma: no cover — never invoked
    raise AssertionError("no task should have been dispatched")
