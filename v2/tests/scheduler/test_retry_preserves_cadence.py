"""STEER-5 (F113) — a transient failure doesn't knock a recurring job off cadence.

F113: on a handler failure under the retry cap, ``_run_job`` set
``next_run_at = now + 5min`` and status='pending'. For a RECURRING job this
clobbered the scheduled cadence — a ``daily@08:00`` brief that fails at 08:00
would retry at ~08:05/08:10 and only AFTER a success recompute the proper
next_run, having lost the canonical slot.

The fix: a separate ``retry_at`` column holds the retry slot; the canonical
``next_run_at`` (the recurring cadence) is NEVER overwritten by a retry. The poll
selects a job due on EITHER its retry_at OR its next_run_at. On success the
``retry_count`` is reset, ``retry_at`` cleared, and ``next_run_at`` recomputed
from the schedule — punctual next occurrence preserved.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.scheduler.base import HandlerRegistry, JobHandler
from stackowl.scheduler.job import Job, JobResult
from stackowl.scheduler.scheduler import JobScheduler

pytestmark = pytest.mark.asyncio


@pytest.fixture()
async def migrated_db(tmp_path: Path) -> AsyncIterator[DbPool]:
    db_path = tmp_path / "sched.db"
    MigrationRunner(db_path=db_path).run()
    pool = DbPool(db_path=db_path)
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


@pytest.fixture(autouse=True)
def _reset_registry() -> object:
    HandlerRegistry.reset()
    yield None
    HandlerRegistry.reset()


class _FlakyHandler(JobHandler):
    """Fails the first ``fail_times`` executions, then succeeds."""

    def __init__(self, *, fail_times: int) -> None:
        self._fail_times = fail_times
        self.runs = 0

    @property
    def handler_name(self) -> str:
        return "goal_execution"

    async def execute(self, job: Job) -> JobResult:
        self.runs += 1
        if self.runs <= self._fail_times:
            return JobResult(
                job_id=job.job_id, success=False, output=None,
                error="transient", duration_ms=1.0,
            )
        return JobResult(job_id=job.job_id, success=True, output="ok", error=None, duration_ms=1.0)


async def _seed_recurring_due(db: DbPool, *, next_run: str) -> str:
    sched = JobScheduler(db=db, tz="UTC")
    job = await sched.create_job(handler_name="goal_execution", schedule="daily@08:00")
    await db.execute(
        "UPDATE jobs SET next_run_at = ?, status = 'pending' WHERE job_id = ?",
        (next_run, job.job_id),
    )
    return job.job_id


async def test_retry_does_not_clobber_recurring_next_run(migrated_db: DbPool) -> None:
    """A failed recurring job retries via retry_at, leaving the canonical cadence intact."""
    handler = _FlakyHandler(fail_times=1)
    HandlerRegistry.instance().register(handler)
    canonical = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    job_id = await _seed_recurring_due(migrated_db, next_run=canonical)

    sched = JobScheduler(db=migrated_db, tz="UTC")
    await sched._poll()  # first run FAILS → schedules a retry

    row = (await migrated_db.fetch_all(
        "SELECT next_run_at, retry_at, retry_count, status FROM jobs WHERE job_id = ?",
        (job_id,),
    ))[0]
    # The canonical cadence slot is UNTOUCHED by the retry.
    assert row["next_run_at"] == canonical, "retry must NOT overwrite the recurring next_run_at"
    # A separate retry slot was set ~5min out.
    assert row["retry_at"] is not None, "a retry slot must be tracked separately"
    assert int(row["retry_count"]) == 1
    assert row["status"] == "pending"


async def test_success_resets_retry_and_recomputes_cadence(migrated_db: DbPool) -> None:
    """On success retry_count resets, retry_at clears, next_run recomputed from schedule."""
    handler = _FlakyHandler(fail_times=1)
    HandlerRegistry.instance().register(handler)
    canonical = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    job_id = await _seed_recurring_due(migrated_db, next_run=canonical)

    sched = JobScheduler(db=migrated_db, tz="UTC")
    await sched._poll()  # fails → retry_at set

    # Force the retry slot due, then poll again — the handler now succeeds.
    await migrated_db.execute(
        "UPDATE jobs SET retry_at = ? WHERE job_id = ?",
        ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(), job_id),
    )
    await sched._poll()  # second run SUCCEEDS

    row = (await migrated_db.fetch_all(
        "SELECT next_run_at, retry_at, retry_count, status FROM jobs WHERE job_id = ?",
        (job_id,),
    ))[0]
    assert int(row["retry_count"]) == 0, "retry_count must reset on success"
    assert row["retry_at"] is None, "retry slot must clear on success"
    # next_run_at recomputed from the schedule — a FUTURE daily@08:00, not now+5min.
    assert row["next_run_at"] != canonical
    assert datetime.fromisoformat(row["next_run_at"]) > datetime.now(UTC)
    assert handler.runs == 2


async def test_retry_slot_makes_job_due_for_poll(migrated_db: DbPool) -> None:
    """A job due ONLY on retry_at (canonical next_run in the future) is still polled."""
    handler = _FlakyHandler(fail_times=0)  # always succeeds
    HandlerRegistry.instance().register(handler)
    future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    job_id = await _seed_recurring_due(migrated_db, next_run=future)
    # Canonical slot is in the FUTURE; only the retry_at slot is due.
    await migrated_db.execute(
        "UPDATE jobs SET retry_at = ?, retry_count = 1 WHERE job_id = ?",
        ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(), job_id),
    )

    await JobScheduler(db=migrated_db, tz="UTC")._poll()
    assert handler.runs == 1, "a job due only on retry_at must be selected by the poll"
