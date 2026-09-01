"""A slow-but-legitimate handler must not delay every OTHER due job's dispatch.

Live incident (2026-07-09): telegram_canary_send health alerts flapped every
~70-90 minutes with an identical "no successful send confirmed" message.
Real notification_log data showed canary firing on a clean 20m/20m/~50m
rhythm — every third cycle delayed by ~30 minutes, locked to dream_worker's
own 30-minute cadence. Root cause: ``_poll`` dispatched due jobs one at a
time (``for row in rows: await self._run_job(...)``), so whenever
dream_worker's legitimate (non-hung) ~20-30min run overlapped a poll cycle,
it silently blocked canary's dispatch for its entire duration — no timeout,
no error, just a late send. This proves the fix: due jobs now dispatch via
``asyncio.gather``, so a slow sibling can no longer delay an unrelated job.

HOW IT MEASURED THAT, AND WHY THAT WAS REWRITTEN 2026-09-01. The test failed once
in a 591-test run and passed both alone (8/8) and on an identical re-run, which
is the signature of a wall-clock assertion on a loaded box rather than a real
regression. Looking at why exposed something worse than a flake:

* ``assert fast.ran_at - t0 < _SLOW_SECONDS / 2`` gave the fast handler a **150
  millisecond absolute budget** to be dispatched. Under load, opening the pool,
  inserting two jobs and polling can burn that on scheduling latency alone — so
  the test failed for a reason unrelated to the property it names.
* ``assert total < _SLOW_SECONDS * 1.8``, whose own comment called it "the
  definitive proof concurrency actually happened", was **VACUOUS**. MEASURED:
  sequential is 0.301s and concurrent is 0.301s, because the fast handler takes
  no time — both pass ``< 0.54``. It could never have failed, in either
  direction, and the incident it was written for would have slipped past it.

So one assertion was fragile and the other could not discriminate at all: the
test was flaky in the direction that cost nothing and blind in the direction
that mattered.

WHAT IT ASSERTS NOW: both handlers occupy time and record their own start and
end, and the test asserts their INTERVALS OVERLAP. Sequential execution cannot
produce overlapping intervals whichever job is dispatched first, and concurrent
execution always does — verified both ways before this was written. There is no
wall-clock threshold left to be wrong about on a loaded machine, and the only
absolute bound remaining is the ``asyncio.wait_for`` hang guard.

THE ORDERING HOLE THE OLD VERSION ALSO HAD: it depended on the SLOW job being
dispatched first. Nothing controlled that — the rows come back in whatever order
the query returns them — and if the fast job had come first, its 150ms assertion
would have passed even under fully sequential dispatch. Interval overlap is
order-independent by construction.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from stackowl.db.pool import DbPool
from stackowl.scheduler.base import HandlerRegistry, JobHandler
from stackowl.scheduler.job import Job, JobResult
from stackowl.scheduler.scheduler import JobScheduler
from stackowl.scheduler.scheduler_helpers import insert_job

pytestmark = pytest.mark.asyncio

_SLOW_SECONDS = 0.3


class _TimedHandler(JobHandler):
    """A handler that occupies time and records the INTERVAL it occupied.

    Both jobs use this. The old version paired a slow handler with an instant
    one, which is why the total-duration assertion could not discriminate: an
    instant handler adds nothing to a sequential total either.
    """

    def __init__(self, name: str) -> None:
        self._name = name
        self.calls = 0
        self.started_at: float | None = None
        self.finished_at: float | None = None

    @property
    def handler_name(self) -> str:
        return self._name

    @property
    def trigger_kind(self) -> str:  # type: ignore[override]
        return "on_demand"

    async def execute(self, job: Job) -> JobResult:
        self.calls += 1
        self.started_at = time.monotonic()
        await asyncio.sleep(_SLOW_SECONDS)
        self.finished_at = time.monotonic()
        return JobResult(job_id=job.job_id, success=True, output="ok", error=None, duration_ms=0.0)


def _overlapped(a: _TimedHandler, b: _TimedHandler) -> bool:
    """True when the two handlers were in flight at the same moment.

    The whole assertion, and it is a RELATIVE one: no wall-clock budget, so a
    loaded machine changes the durations without changing the verdict.
    """
    assert a.started_at is not None and a.finished_at is not None
    assert b.started_at is not None and b.finished_at is not None
    return a.started_at < b.finished_at and b.started_at < a.finished_at


def _job(handler: str, **overrides: Any) -> Job:
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    defaults: dict[str, Any] = dict(
        job_id=f"{handler}-{uuid.uuid4().hex[:6]}",
        handler_name=handler,
        schedule="every 20m",
        idempotency_key=uuid.uuid4().hex,
        last_run_at=None,
        next_run_at=past,
        status="pending",
        params={},
    )
    defaults.update(overrides)
    return Job(**defaults)


@pytest.fixture(autouse=True)
def _reset_registry() -> Any:
    HandlerRegistry.reset()
    yield
    HandlerRegistry.reset()


@pytest.fixture(autouse=True)
def _allow_scheduler(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "stackowl.config.test_mode.TestModeGuard.assert_not_test_mode",
        lambda *_a, **_kw: None,
    )


async def test_a_slow_job_does_not_delay_its_sibling(tmp_db: DbPool) -> None:
    """The 2026-07-09 incident, asserted by OVERLAP rather than by the clock."""
    reg = HandlerRegistry.instance()
    dream = _TimedHandler("dream_worker")
    canary = _TimedHandler("telegram_canary")
    reg.register(dream)
    reg.register(canary)
    sched = JobScheduler(db=tmp_db, handler_registry=reg)

    await insert_job(tmp_db, _job("dream_worker"))
    await insert_job(tmp_db, _job("telegram_canary"))

    # The only absolute bound left, and it is a HANG guard rather than a
    # concurrency assertion — sequential dispatch of two 0.3s jobs finishes well
    # inside it, so this can never be what fails when concurrency breaks.
    await asyncio.wait_for(sched._poll(), timeout=5.0)

    assert dream.calls == 1
    assert canary.calls == 1
    assert _overlapped(dream, canary), (
        "the two due jobs did not overlap — they were dispatched one after the "
        "other, which is the 2026-07-09 incident: a legitimate slow handler "
        "silently delaying an unrelated job's entire run"
    )


async def test_the_overlap_check_can_actually_FAIL(tmp_db: DbPool) -> None:
    """The guard on the guard.

    The assertion this test file rests on was replaced because the previous one
    passed for sequential AND concurrent code (measured: 0.301s both ways). A
    check that cannot fail is worse than no check, so this drives the two
    handlers sequentially and asserts the verdict flips.
    """
    a = _TimedHandler("a")
    b = _TimedHandler("b")
    job = _job("a")
    await a.execute(job)
    await b.execute(job)
    assert _overlapped(a, b) is False, (
        "sequential execution reported as overlapping — the concurrency "
        "assertion cannot distinguish the bug from the fix"
    )
