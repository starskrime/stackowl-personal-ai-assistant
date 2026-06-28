"""S11c — a scheduled owl's job is paused after 3 consecutive failures + ONE alert.

Drives the scheduler's failure path directly: an owl-lifecycle job (provenance
marker ``source='owl_lifecycle'``) re-arms on the first two occurrence failures
(silently), then on the third consecutive failure is PAUSED (status=failed,
enabled=0) and exactly ONE operator notification is delivered.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.scheduler.scheduler import JobScheduler
from stackowl.scheduler.scheduler_helpers import row_to_job

pytestmark = pytest.mark.asyncio


class _StubOutcome:
    rollup = "delivered"


class _StubDeliverer:
    """Counts deliver_for_job calls (the operator failure alert)."""

    def __init__(self) -> None:
        self.calls = 0

    async def deliver_for_job(self, job: Any, **_: Any) -> _StubOutcome:
        self.calls += 1
        return _StubOutcome()


@pytest.fixture()
async def db(tmp_path: Path) -> AsyncIterator[DbPool]:
    db_path = tmp_path / "sched.db"
    MigrationRunner(db_path=db_path).run()
    pool = DbPool(db_path=db_path)
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


async def _reload(db: DbPool, job_id: str) -> Any:
    rows = await db.fetch_all("SELECT * FROM jobs WHERE job_id = ?", (job_id,))
    return row_to_job(rows[0])


async def test_three_consecutive_failures_pause_owl_job_with_one_alert(db: DbPool) -> None:
    deliverer = _StubDeliverer()
    sched = JobScheduler(db=db, job_deliverer=deliverer)
    job = await sched.create_job(
        handler_name="goal_execution",
        schedule="every 10m",
        params={"source": "owl_lifecycle", "owner": "watcher", "goal": "x"},
    )

    # Failure 1 → re-arm, still enabled, no alert.
    await sched._mark_failed(await _reload(db, job.job_id), last_error="boom")
    j1 = await _reload(db, job.job_id)
    assert j1.status == "pending" and j1.enabled is True
    assert j1.failure_count == 1
    assert deliverer.calls == 0

    # Failure 2 → re-arm, still enabled, no alert.
    await sched._mark_failed(await _reload(db, job.job_id), last_error="boom")
    j2 = await _reload(db, job.job_id)
    assert j2.status == "pending" and j2.enabled is True
    assert j2.failure_count == 2
    assert deliverer.calls == 0

    # Failure 3 → CIRCUIT BREAK: paused (failed + disabled), exactly ONE alert.
    await sched._mark_failed(await _reload(db, job.job_id), last_error="boom")
    j3 = await _reload(db, job.job_id)
    assert j3.status == "failed" and j3.enabled is False
    assert j3.failure_count == 3
    assert deliverer.calls == 1


async def test_success_resets_consecutive_failure_count(db: DbPool) -> None:
    sched = JobScheduler(db=db)
    job = await sched.create_job(
        handler_name="goal_execution",
        schedule="every 10m",
        params={"source": "owl_lifecycle", "owner": "watcher", "goal": "x"},
    )
    await sched._mark_failed(await _reload(db, job.job_id), last_error="boom")
    assert (await _reload(db, job.job_id)).failure_count == 1
    # A successful run closes the breaker — failure_count back to 0.
    from stackowl.scheduler.job import JobResult

    reloaded = await _reload(db, job.job_id)
    await sched._mark_completed(
        reloaded, JobResult(job_id=job.job_id, success=True, output="ok", error=None, duration_ms=1.0), 1.0
    )
    assert (await _reload(db, job.job_id)).failure_count == 0
