"""Generalized recurring-job failure handling — ANY recurring job (not just
owl-lifecycle ones), no consecutive-failure circuit breaker (owner decision
2026-07-22).

Live incident (2026-07-12): a user-created ``goal_execution`` cronjob hit the
SAME ``budget:stop:steps:limit=20.0:actual=20.0`` cap on every occurrence.
Before the circuit-breaker fix, ``_mark_failed`` only paused a job carrying
``params['source'] == 'owl_lifecycle'`` — a plain user cronjob had no such
marker, so it fell through to the unconditional re-arm+notify path and
looped/spammed forever. The circuit breaker (S11c) was later removed
entirely (owner decision 2026-07-22, tool-count is the only limit kept) — a
recurring job now re-arms forever regardless of how many times it fails, and
gets an operator alert on EVERY re-arm (not just a one-time pause alert), so
ongoing trouble is never silent but the job itself is never given up on.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.scheduler.job import JobResult
from stackowl.scheduler.scheduler import JobScheduler
from stackowl.scheduler.scheduler_helpers import row_to_job

pytestmark = pytest.mark.asyncio


class _StubOutcome:
    rollup = "delivered"


class _StubDeliverer:
    """Counts + records deliver_for_job calls (the operator failure alert)."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def deliver_for_job(self, job: Any, *, message: str, **_: Any) -> _StubOutcome:
        self.calls.append(message)
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


async def test_non_owl_recurring_job_rearms_forever_with_alert_every_time(
    db: DbPool,
) -> None:
    """A plain user cronjob (no owl-lifecycle 'source' marker) that structurally
    cannot succeed (e.g. always hits the same step-budget cap) must keep
    RE-ARMING onto its next slot no matter how many times it fails — it is never
    permanently paused — and must alert on EVERY failure (not just once)."""
    deliverer = _StubDeliverer()
    sched = JobScheduler(db=db, job_deliverer=deliverer)
    job = await sched.create_job(
        handler_name="goal_execution",
        schedule="every 10m",
        params={"goal": "research something that always blows the step budget"},
    )
    err = "budget:stop:steps:limit=20.0:actual=20.0"

    for expected_count in (1, 2, 3, 4, 5):
        await sched._mark_failed(await _reload(db, job.job_id), last_error=err)
        reloaded = await _reload(db, job.job_id)
        assert reloaded.status == "pending" and reloaded.enabled is True
        assert reloaded.failure_count == expected_count

    # Every single re-arm sent its own alert — never silently swallowed after
    # some threshold, since there is no longer a threshold.
    assert len(deliverer.calls) == 5
    for message in deliverer.calls:
        assert "is failing repeatedly" in message
        assert err in message


async def test_success_resets_counter_so_unrelated_later_failures_dont_wrongly_accumulate(
    db: DbPool,
) -> None:
    """Fail, succeed, fail again — the intervening success must reset
    failure_count to 0 so the second failure streak starts clean."""
    sched = JobScheduler(db=db)
    job = await sched.create_job(
        handler_name="goal_execution",
        schedule="every 10m",
        params={"goal": "usually fine, occasionally flaky"},
    )

    # One failure.
    await sched._mark_failed(await _reload(db, job.job_id), last_error="transient boom")
    assert (await _reload(db, job.job_id)).failure_count == 1

    # A success resets the counter.
    reloaded = await _reload(db, job.job_id)
    await sched._mark_completed(
        reloaded,
        JobResult(job_id=job.job_id, success=True, output="ok", error=None, duration_ms=1.0),
        1.0,
    )
    assert (await _reload(db, job.job_id)).failure_count == 0

    # A second, unrelated failure must count as failure #1 of a NEW streak, not
    # failure #2 of the old one — still enabled either way, since there is no
    # pause threshold anymore.
    await sched._mark_failed(await _reload(db, job.job_id), last_error="transient boom again")
    after = await _reload(db, job.job_id)
    assert after.failure_count == 1
    assert after.status == "pending" and after.enabled is True
