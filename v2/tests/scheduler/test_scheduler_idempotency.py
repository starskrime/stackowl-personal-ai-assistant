"""Regression tests for occurrence-scoped scheduler idempotency (Bug A).

The scheduler keyed its ``job_runs`` dedup on the STATIC ``jobs.idempotency_key``
(e.g. ``dream_worker:nightly``). On the first successful run it wrote a
``status='completed'`` row under that constant key; every later poll then matched
it and logged an "idempotent skip" *before* the line that advances
``next_run_at`` — so a recurring job ran exactly once, ever, then was skipped
forever. A constant key means "run once ever", which is wrong for a recurring
job.

The fix scopes the dedup key to the *occurrence* being serviced
(``f"{idempotency_key}@{next_run_at}"``): the same scheduled instant is deduped,
but each new scheduled instant is a fresh occurrence that fires. These tests pin
that behaviour for both the poller (``_run_job``/``_mark_completed``) and the
out-of-band ``run_now`` path (``_record_run``).
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


class _CountingHandler(JobHandler):
    """A goal_execution stand-in that records every job it is asked to run."""

    def __init__(self) -> None:
        self.runs: list[str] = []

    @property
    def handler_name(self) -> str:
        return "goal_execution"

    async def execute(self, job: Job) -> JobResult:
        self.runs.append(job.job_id)
        return JobResult(
            job_id=job.job_id, success=True, output="ok", error=None, duration_ms=1.0
        )


async def _seed_recurring_job(db: DbPool, *, schedule: str = "every 1m") -> str:
    """Create a recurring job and force ``next_run_at`` into the past (due now)."""
    sched = JobScheduler(db=db)
    job = await sched.create_job(handler_name="goal_execution", schedule=schedule)
    past = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    await db.execute(
        "UPDATE jobs SET next_run_at = ?, status = 'pending' WHERE job_id = ?",
        (past, job.job_id),
    )
    return job.job_id


async def _set_next_run(db: DbPool, job_id: str, when: datetime) -> None:
    await db.execute(
        "UPDATE jobs SET next_run_at = ?, status = 'pending' WHERE job_id = ?",
        (when.isoformat(), job_id),
    )


async def _poll(db: DbPool) -> None:
    await JobScheduler(db=db)._poll()


# ----------------------------------------------------------------- occurrence fires


async def test_recurring_job_fires_on_each_due_occurrence(migrated_db: DbPool) -> None:
    """Two distinct due occurrences must BOTH fire (RED today: fires once then skips forever)."""
    handler = _CountingHandler()
    HandlerRegistry.instance().register(handler)
    job_id = await _seed_recurring_job(migrated_db, schedule="every 1m")

    # First occurrence: due in the past, fires.
    await _set_next_run(migrated_db, job_id, datetime.now(UTC) - timedelta(minutes=10))
    await _poll(migrated_db)

    # Second occurrence: a DIFFERENT scheduled instant, still in the past, must fire again.
    await _set_next_run(migrated_db, job_id, datetime.now(UTC) - timedelta(minutes=2))
    await _poll(migrated_db)

    assert handler.runs == [job_id, job_id], "a recurring job must fire on each due occurrence"
    runs = await migrated_db.fetch_all(
        "SELECT idempotency_key FROM job_runs WHERE job_id = ? AND status = 'completed'",
        (job_id,),
    )
    assert len(runs) == 2, "two distinct occurrences => two distinct job_runs rows"
    assert len({r["idempotency_key"] for r in runs}) == 2, "each occurrence has a distinct key"


async def test_same_occurrence_not_rerun(migrated_db: DbPool) -> None:
    """Dispatching the SAME occurrence twice (same next_run_at) dedups to one run."""
    handler = _CountingHandler()
    HandlerRegistry.instance().register(handler)
    job_id = await _seed_recurring_job(migrated_db, schedule="every 1m")

    fixed = datetime.now(UTC) - timedelta(minutes=10)
    await _set_next_run(migrated_db, job_id, fixed)
    await _poll(migrated_db)

    # Re-arm the EXACT same occurrence instant (no advance) and poll again.
    await _set_next_run(migrated_db, job_id, fixed)
    await _poll(migrated_db)

    assert handler.runs == [job_id], "same occurrence must run exactly once (genuine dedup)"
    runs = await migrated_db.fetch_all(
        "SELECT run_id FROM job_runs WHERE job_id = ? AND status = 'completed'", (job_id,)
    )
    assert len(runs) == 1


async def test_occurrence_key_differs_by_scheduled_time(migrated_db: DbPool) -> None:
    """Unit-test ``_occurrence_key``: same key iff same next_run_at."""
    sched = JobScheduler(db=migrated_db)
    base = Job(
        job_id="goal_execution-1",
        handler_name="goal_execution",
        schedule="every 1m",
        idempotency_key="goal_execution:goal_execution-1",
        last_run_at=None,
        next_run_at="2026-06-01T00:00:00+00:00",
        status="pending",
    )
    later = base.model_copy(update={"next_run_at": "2026-06-01T00:01:00+00:00"})
    same = base.model_copy(update={"next_run_at": "2026-06-01T00:00:00+00:00"})

    assert sched._occurrence_key(base) != sched._occurrence_key(later)
    assert sched._occurrence_key(base) == sched._occurrence_key(same)


async def test_run_now_does_not_poison_next_poll(migrated_db: DbPool) -> None:
    """After run_now completes a job, a later poll at the NEXT occurrence still fires it."""
    handler = _CountingHandler()
    HandlerRegistry.instance().register(handler)
    job_id = await _seed_recurring_job(migrated_db, schedule="every 1m")

    # Out-of-band run at occurrence T0.
    await _set_next_run(migrated_db, job_id, datetime.now(UTC) - timedelta(minutes=10))
    result = await JobScheduler(db=migrated_db).run_now(job_id)
    assert result is not None and result.success is True
    assert handler.runs == [job_id]

    # A later poll at a DIFFERENT occurrence must still fire (run_now must not poison it).
    await _set_next_run(migrated_db, job_id, datetime.now(UTC) - timedelta(minutes=2))
    await _poll(migrated_db)

    assert handler.runs == [job_id, job_id], "run_now occurrence must not poison the next poll"
