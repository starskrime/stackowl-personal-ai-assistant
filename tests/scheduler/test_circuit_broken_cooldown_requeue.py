"""Circuit-broken jobs get ONE fresh attempt after a cooldown, not death forever.

Live incident (2026-07-18): 6 otherwise-healthy recurring goal_execution/
reflection_writer/dream_worker jobs circuit-broke (S11c, 3 consecutive
failures) because of a shared, transient upstream outage (the tracked
NeraAiRaw tool-calling gateway gap) and stayed permanently disabled with no
way back except a manual ``/cronjob resume``. This locks in the fix: a
cooldown-based auto-requeue (mirrors ObjectiveDriverHandler's F-41 transient-
block requeue) that gives a circuit-broken job one fresh attempt once
``_CIRCUIT_BREAKER_COOLDOWN_SEC`` has elapsed since it broke.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.scheduler.scheduler import _CIRCUIT_BREAKER_COOLDOWN_SEC, JobScheduler
from stackowl.scheduler.scheduler_helpers import row_to_job

pytestmark = pytest.mark.asyncio


class _StubOutcome:
    rollup = "delivered"


class _StubDeliverer:
    """Counts + records deliver_for_job calls (the self-heal notice)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def deliver_for_job(self, job: Any, *, message: str, category: str, **_: Any) -> _StubOutcome:
        self.calls.append((category, message))
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


async def _circuit_break(sched: JobScheduler, db: DbPool, job_id: str) -> None:
    """Drive a job through 3 consecutive failures so it circuit-breaks."""
    for _ in range(3):
        await sched._mark_failed(await _reload(db, job_id), last_error="boom")


async def test_circuit_broken_job_auto_requeued_after_cooldown(db: DbPool) -> None:
    sched = JobScheduler(db=db)
    job = await sched.create_job(
        handler_name="goal_execution", schedule="every 10m", params={"goal": "check GOOGL"},
    )
    await _circuit_break(sched, db, job.job_id)
    broken = await _reload(db, job.job_id)
    assert broken.status == "failed" and broken.enabled is False

    # Backdate circuit_broken_at past the cooldown (simulates time having passed).
    stale = (
        datetime.now(UTC) - timedelta(seconds=_CIRCUIT_BREAKER_COOLDOWN_SEC + 60)
    ).isoformat()
    await db.execute(
        "UPDATE jobs SET circuit_broken_at = ? WHERE job_id = ?", (stale, job.job_id),
    )

    requeued = await sched._requeue_circuit_broken()
    assert requeued == 1

    resumed = await _reload(db, job.job_id)
    assert resumed.status == "pending"
    assert resumed.enabled is True
    assert resumed.failure_count == 0


async def test_circuit_broken_job_not_requeued_before_cooldown(db: DbPool) -> None:
    sched = JobScheduler(db=db)
    job = await sched.create_job(
        handler_name="goal_execution", schedule="every 10m", params={"goal": "check GOOGL"},
    )
    await _circuit_break(sched, db, job.job_id)

    requeued = await sched._requeue_circuit_broken()
    assert requeued == 0

    still_broken = await _reload(db, job.job_id)
    assert still_broken.status == "failed"
    assert still_broken.enabled is False


async def test_auto_requeue_notifies_owner_of_the_self_heal(db: DbPool) -> None:
    """The auto-requeue must be OBSERVABLE, not a silent background flip — the
    owner gets told their job came back, mirroring the alert every other
    lifecycle transition (circuit-break, re-arm) already sends."""
    deliverer = _StubDeliverer()
    sched = JobScheduler(db=db, job_deliverer=deliverer)
    job = await sched.create_job(
        handler_name="goal_execution", schedule="every 10m", params={"goal": "check GOOGL"},
    )
    await _circuit_break(sched, db, job.job_id)
    stale = (
        datetime.now(UTC) - timedelta(seconds=_CIRCUIT_BREAKER_COOLDOWN_SEC + 60)
    ).isoformat()
    await db.execute(
        "UPDATE jobs SET circuit_broken_at = ? WHERE job_id = ?", (stale, job.job_id),
    )

    deliverer.calls.clear()  # drop the 3 circuit-break-streak alerts _mark_failed sent
    await sched._requeue_circuit_broken()

    assert len(deliverer.calls) == 1
    category, message = deliverer.calls[0]
    assert category == "job_self_healed"
    assert "automatically resumed" in message
    assert job.job_id in message


async def test_healthy_job_untouched_by_requeue_sweep(db: DbPool) -> None:
    """A job that never circuit-broke (circuit_broken_at IS NULL) is never
    touched by the sweep — the SELECT's IS NOT NULL guard is load-bearing."""
    sched = JobScheduler(db=db)
    job = await sched.create_job(
        handler_name="goal_execution", schedule="every 10m", params={"goal": "check GOOGL"},
    )
    requeued = await sched._requeue_circuit_broken()
    assert requeued == 0
    healthy = await _reload(db, job.job_id)
    assert healthy.status == "pending" and healthy.enabled is True
