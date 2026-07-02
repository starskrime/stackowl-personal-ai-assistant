"""Regression — a run_once handler that self-deletes its job row must not crash
``_mark_completed``'s bookkeeping (QA/Murat finding, live reminder pipeline).

``GoalExecutionHandler.execute`` intentionally ``DELETE``s a ``run_once`` job's
own ``jobs`` row on successful delivery (fire-and-forget agents delete
themselves). The scheduler poll loop (``_run_job`` -> ``_mark_completed``) then
unconditionally tries to ``INSERT INTO job_runs (job_id, ...)`` referencing that
SAME job_id. ``job_runs.job_id`` is a real, enforced ``FOREIGN KEY`` (the
production pool runs with ``PRAGMA foreign_keys=ON``), so the insert raises
``sqlite3.IntegrityError`` — uncaught anywhere up through ``run()``. Each hit
counts as one supervisor consecutive-failure; five successful one-shot
reminders in a row park the WHOLE scheduler (every recurring job too) as
permanently ``failed``.

``job_runs`` is a dedup/history table only (migration 0040's own comment: "no
other table references it") and a deleted one-shot job can never be re-polled,
so skipping its ``job_runs`` row when the job is already gone loses nothing.
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


class _SelfDeletingHandler(JobHandler):
    """Mirrors ``GoalExecutionHandler``'s run_once self-delete-then-return shape:
    the job's own row is gone from ``jobs`` BEFORE the handler returns success."""

    def __init__(self, db: DbPool) -> None:
        self._db = db

    @property
    def handler_name(self) -> str:
        return "goal_execution"

    async def execute(self, job: Job) -> JobResult:
        await self._db.execute("DELETE FROM jobs WHERE job_id = ?", (job.job_id,))
        return JobResult(job_id=job.job_id, success=True, output="ok", error=None, duration_ms=1.0)


async def _seed_run_once_job(db: DbPool) -> str:
    sched = JobScheduler(db=db)
    job = await sched.create_job(
        handler_name="goal_execution",
        schedule="in 1m",
        params={"goal": "remind me", "run_once": True},
    )
    past = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    await db.execute(
        "UPDATE jobs SET next_run_at = ?, status = 'pending' WHERE job_id = ?",
        (past, job.job_id),
    )
    return job.job_id


async def test_poll_survives_handler_self_delete_before_mark_completed(
    migrated_db: DbPool,
) -> None:
    """RED today: FOREIGN KEY constraint failed inserting job_runs for the deleted job_id."""
    handler = _SelfDeletingHandler(migrated_db)
    HandlerRegistry.instance().register(handler)
    job_id = await _seed_run_once_job(migrated_db)

    # Must not raise sqlite3.IntegrityError.
    await JobScheduler(db=migrated_db)._poll()

    rows = await migrated_db.fetch_all("SELECT * FROM jobs WHERE job_id = ?", (job_id,))
    assert rows == [], "the handler's self-delete must stick — no zombie row re-inserted"

    run_rows = await migrated_db.fetch_all(
        "SELECT * FROM job_runs WHERE job_id = ?", (job_id,)
    )
    assert run_rows == [], "no job_runs row can reference a job_id that no longer exists"


async def test_poll_still_records_job_runs_for_a_surviving_job(
    migrated_db: DbPool,
) -> None:
    """Contrast case — a normal (non-self-deleting) completion still writes job_runs."""

    class _CountingHandler(JobHandler):
        @property
        def handler_name(self) -> str:
            return "goal_execution"

        async def execute(self, job: Job) -> JobResult:
            return JobResult(job_id=job.job_id, success=True, output="ok", error=None, duration_ms=1.0)

    HandlerRegistry.instance().register(_CountingHandler())
    job_id = await _seed_run_once_job(migrated_db)

    await JobScheduler(db=migrated_db)._poll()

    run_rows = await migrated_db.fetch_all(
        "SELECT * FROM job_runs WHERE job_id = ? AND status = 'completed'", (job_id,)
    )
    assert len(run_rows) == 1, "a job that still exists at completion keeps its audit row"
