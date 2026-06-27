"""F-63 (S3) — an idempotent skip must still advance the recurring cadence.

The poller dedups an occurrence by ``f"{idempotency_key}@{next_run_at}"``: if a
``status='completed'`` ``job_runs`` row already exists for the occurrence being
serviced, ``_run_job`` logs an "idempotent skip" and returns. Before this fix it
returned WITHOUT advancing ``next_run_at`` — unlike the normal single-dispatch
path (``_mark_completed``), which recomputes the next slot. So a recurring job
whose current occurrence was already recorded (e.g. a lost-race / out-of-band
completion that left the row at its past instant) stayed ``pending`` at that PAST
instant and idempotent-skipped EVERY subsequent poll forever — never verifying
its NEXT occurrence was scheduled.

The fix advances a RECURRING job to its next future slot on an idempotent skip
(reusing ``compute_next_run``), while leaving a ONE-SHOT job untouched (a
completed one-shot must never be re-armed to fire again).
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


@pytest.fixture(autouse=True)
def _bypass_test_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "stackowl.config.test_mode.TestModeGuard.assert_not_test_mode",
        lambda *_a, **_kw: None,
    )


class _CountingHandler(JobHandler):
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


async def _seed_due_job(
    db: DbPool, *, run_once: bool, schedule: str = "every 1m"
) -> tuple[str, str, str]:
    """Seed a due job + a pre-existing completed run for its CURRENT occurrence.

    Returns ``(job_id, past_iso, occurrence_key)``. The completed ``job_runs`` row
    means the next poll will take the idempotent-skip branch without advancing.
    """
    sched = JobScheduler(db=db)
    job = await sched.create_job(
        handler_name="goal_execution",
        schedule=schedule,
        params={"run_once": True} if run_once else None,
    )
    past = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    await db.execute(
        "UPDATE jobs SET next_run_at = ?, status = 'pending' WHERE job_id = ?",
        (past, job.job_id),
    )
    occurrence_key = f"{job.idempotency_key}@{past}"
    await db.execute(
        "INSERT INTO job_runs (run_id, job_id, idempotency_key, status, duration_ms, ran_at) "
        "VALUES (?,?,?,?,?,?)",
        ("pre-run", job.job_id, occurrence_key, "completed", 1.0, past),
    )
    return job.job_id, past, occurrence_key


async def test_idempotent_skip_advances_recurring_job(migrated_db: DbPool) -> None:
    """RED today: the skipped recurring job stays pending at its PAST instant forever."""
    handler = _CountingHandler()
    HandlerRegistry.instance().register(handler)
    job_id, past, _ = await _seed_due_job(migrated_db, run_once=False)

    await JobScheduler(db=migrated_db)._poll()

    # The handler must NOT re-run (genuine dedup is preserved)...
    assert handler.runs == [], "an already-serviced occurrence must not re-run"
    rows = await migrated_db.fetch_all(
        "SELECT status, next_run_at FROM jobs WHERE job_id = ?", (job_id,)
    )
    assert rows[0]["status"] == "pending"
    # ...but next_run_at must be advanced into the FUTURE so the job is no longer
    # stuck idempotent-skipping the same past instant every poll.
    advanced = datetime.fromisoformat(rows[0]["next_run_at"])
    assert advanced > datetime.now(UTC), "recurring idempotent-skip must advance next_run_at"
    assert rows[0]["next_run_at"] != past


async def test_idempotent_skip_does_not_rearm_one_shot(migrated_db: DbPool) -> None:
    """A completed one-shot is done — an idempotent skip must NOT re-arm it."""
    handler = _CountingHandler()
    HandlerRegistry.instance().register(handler)
    job_id, past, _ = await _seed_due_job(migrated_db, run_once=True)

    await JobScheduler(db=migrated_db)._poll()

    assert handler.runs == [], "one-shot occurrence must not re-run"
    rows = await migrated_db.fetch_all(
        "SELECT next_run_at FROM jobs WHERE job_id = ?", (job_id,)
    )
    assert rows[0]["next_run_at"] == past, "one-shot must NOT be re-armed to a new slot"


async def test_idempotent_skip_no_extra_job_run_row(migrated_db: DbPool) -> None:
    """Advancing the slot must not write a second job_runs row (no fake re-completion)."""
    handler = _CountingHandler()
    HandlerRegistry.instance().register(handler)
    job_id, _, _ = await _seed_due_job(migrated_db, run_once=False)

    await JobScheduler(db=migrated_db)._poll()

    runs = await migrated_db.fetch_all(
        "SELECT run_id FROM job_runs WHERE job_id = ?", (job_id,)
    )
    assert len(runs) == 1, "no new run row — only the slot advances on an idempotent skip"
