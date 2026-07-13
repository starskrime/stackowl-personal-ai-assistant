"""Generalized circuit-breaker — ANY recurring job (not just owl-lifecycle ones).

Live incident (2026-07-12): a user-created ``goal_execution`` cronjob hit the
SAME ``budget:stop:steps:limit=20.0:actual=20.0`` cap on every occurrence.
Before this fix, ``_mark_failed``'s circuit-breaker (S11c) only paused a job
carrying ``params['source'] == 'owl_lifecycle'`` — a plain user cronjob has no
such marker, so it fell through to the unconditional re-arm+notify path and
looped/spammed forever. This suite locks in the generalized behavior: ANY
recurring job pauses after ``MAX_CONSECUTIVE_FAILURES`` consecutive full-cycle
failures, with exactly ONE alert (not one per cycle), and a success resets the
counter so an unrelated later failure streak starts clean.
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


async def test_non_owl_recurring_job_circuit_breaks_after_threshold_with_one_alert(
    db: DbPool,
) -> None:
    """A plain user cronjob (no owl-lifecycle 'source' marker) that structurally
    cannot succeed (e.g. always hits the same step-budget cap) must be PAUSED
    after MAX_CONSECUTIVE_FAILURES, not re-armed forever — and must send exactly
    ONE alert across the whole streak, not one per cycle."""
    deliverer = _StubDeliverer()
    sched = JobScheduler(db=db, job_deliverer=deliverer)
    job = await sched.create_job(
        handler_name="goal_execution",
        schedule="every 10m",
        params={"goal": "research something that always blows the step budget"},
    )
    err = "budget:stop:steps:limit=20.0:actual=20.0"

    # Failures 1 and 2 — still under threshold: re-armed, each sends its own
    # per-re-arm alert (pre-existing F-61 behavior, unchanged for non-owl jobs).
    await sched._mark_failed(await _reload(db, job.job_id), last_error=err)
    j1 = await _reload(db, job.job_id)
    assert j1.status == "pending" and j1.enabled is True
    assert j1.failure_count == 1

    await sched._mark_failed(await _reload(db, job.job_id), last_error=err)
    j2 = await _reload(db, job.job_id)
    assert j2.status == "pending" and j2.enabled is True
    assert j2.failure_count == 2

    # Failure 3 — CIRCUIT BREAK: paused (failed + disabled), one final alert.
    await sched._mark_failed(await _reload(db, job.job_id), last_error=err)
    j3 = await _reload(db, job.job_id)
    assert j3.status == "failed" and j3.enabled is False
    assert j3.failure_count == 3

    # A 4th poll tick would never even reach _mark_failed again — the job is no
    # longer selected by `WHERE status='pending' AND enabled=1`. The alert count
    # across the whole streak is bounded (3, matching the 3 distinct cycles),
    # never unbounded/forever, and the LAST alert is the actionable pause message.
    assert deliverer.calls, "must have alerted at least once"
    last_message = deliverer.calls[-1]
    assert "PAUSED" in last_message
    assert err in last_message
    assert "/cronjob" in last_message
    assert job.job_id in last_message


async def test_success_resets_counter_so_unrelated_later_failures_dont_wrongly_pause(
    db: DbPool,
) -> None:
    """Fail, succeed, fail again — the intervening success must reset
    failure_count to 0 so the second failure streak starts clean and does NOT
    inherit the first streak's count toward the pause threshold."""
    sched = JobScheduler(db=db)
    job = await sched.create_job(
        handler_name="goal_execution",
        schedule="every 10m",
        params={"goal": "usually fine, occasionally flaky"},
    )

    # One failure.
    await sched._mark_failed(await _reload(db, job.job_id), last_error="transient boom")
    assert (await _reload(db, job.job_id)).failure_count == 1

    # A success closes the breaker.
    reloaded = await _reload(db, job.job_id)
    await sched._mark_completed(
        reloaded,
        JobResult(job_id=job.job_id, success=True, output="ok", error=None, duration_ms=1.0),
        1.0,
    )
    assert (await _reload(db, job.job_id)).failure_count == 0

    # A second, unrelated failure must count as failure #1 of a NEW streak, not
    # failure #2 of the old one — still well under threshold, still enabled.
    await sched._mark_failed(await _reload(db, job.job_id), last_error="transient boom again")
    after = await _reload(db, job.job_id)
    assert after.failure_count == 1
    assert after.status == "pending" and after.enabled is True
