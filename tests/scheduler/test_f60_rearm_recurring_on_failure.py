"""F-60 (S1) — a recurring proactive job must NOT go dark after transient failures.

Before the fix, ``_mark_failed`` set ``status='failed'`` unconditionally once the
within-occurrence retries exhausted. The poll select / ``recover`` / ``reap`` all
ignore terminal ``failed`` rows, so a recurring job (morning_brief, check_in) that
hit 3 transient failures never recomputed ``next_run_at`` and silently died.

The fix: ``_mark_failed`` distinguishes RECURRING from ONE-SHOT (via the explicit
``params['run_once']`` marker the rest of the scheduler already keys on) and, for a
recurring job, RE-ARMS — advancing ``next_run_at`` to the next cadence slot,
resetting the retry counter, and returning ``status='pending'`` — plus an audit row.
One-shots stay terminal ``failed``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from stackowl.db.pool import DbPool
from stackowl.scheduler.base import HandlerRegistry, JobHandler
from stackowl.scheduler.job import Job, JobResult
from stackowl.scheduler.scheduler import _MAX_RETRIES, JobScheduler
from stackowl.scheduler.scheduler_helpers import insert_job

pytestmark = pytest.mark.asyncio


class _AlwaysFailsHandler(JobHandler):
    """A handler that always returns success=False — a transiently-broken job."""

    def __init__(self, name: str) -> None:
        self._name = name
        self.calls = 0

    @property
    def handler_name(self) -> str:
        return self._name

    @property
    def trigger_kind(self) -> str:  # type: ignore[override]
        return "on_demand"

    async def execute(self, job: Job) -> JobResult:
        self.calls += 1
        return JobResult(
            job_id=job.job_id,
            success=False,
            output=None,
            error="transient boom",
            duration_ms=1.0,
        )


def _job(handler: str, *, params: dict[str, Any] | None = None, **overrides: Any) -> Job:
    # next_run_at in the PAST so the job is due now.
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    defaults: dict[str, Any] = dict(
        job_id=f"{handler}-{uuid.uuid4().hex[:6]}",
        handler_name=handler,
        schedule="daily@08:00",
        idempotency_key=uuid.uuid4().hex,
        last_run_at=None,
        next_run_at=past,
        status="pending",
        params=params or {},
    )
    defaults.update(overrides)
    return Job(**defaults)


@pytest.fixture(autouse=True)
def _reset_registry() -> Any:
    HandlerRegistry.reset()
    yield
    HandlerRegistry.reset()


def _sched(db: DbPool, handler: JobHandler) -> JobScheduler:
    reg = HandlerRegistry.instance()
    reg.register(handler)
    return JobScheduler(db=db, handler_registry=reg)


async def _exhaust_retries(db: DbPool, sched: JobScheduler, job_id: str) -> None:
    """Drive the failing job through enough poll cycles to exhaust retries.

    Each failing run either bumps retry_count + sets a future retry_at, or (on the
    final attempt) reaches the max-retries branch. We clear retry_at and re-due the
    row between cycles so the next poll re-dispatches it without waiting 5 minutes.
    """
    now = datetime.now(UTC)
    for _ in range(_MAX_RETRIES + 2):
        past = (now - timedelta(minutes=1)).isoformat()
        await db.execute(
            "UPDATE jobs SET next_run_at = ?, retry_at = NULL WHERE job_id = ? "
            "AND status = 'pending'",
            (past, job_id),
        )
        await sched._poll()
        rows = await db.fetch_all(
            "SELECT status, next_run_at FROM jobs WHERE job_id = ?", (job_id,)
        )
        if not rows:
            return
        # Terminal failed -> stop (one-shot path).
        if rows[0]["status"] == "failed":
            return
        # Re-armed -> pending with next_run_at advanced into the FUTURE (recurring
        # path). Stop here without re-dueing, so the asserted state is the re-arm
        # itself, not a subsequent extra failure.
        if datetime.fromisoformat(rows[0]["next_run_at"]) > now:
            return


async def test_recurring_job_rearmed_after_max_retries(
    tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "stackowl.config.test_mode.TestModeGuard.assert_not_test_mode",
        lambda *_a, **_kw: None,
    )
    handler = _AlwaysFailsHandler("morning_brief")
    sched = _sched(tmp_db, handler)
    job = _job("morning_brief")  # recurring (no run_once)
    await insert_job(tmp_db, job)

    await _exhaust_retries(tmp_db, sched, job.job_id)

    rows = await tmp_db.fetch_all(
        "SELECT status, retry_count, next_run_at, retry_at FROM jobs WHERE job_id = ?",
        (job.job_id,),
    )
    assert len(rows) == 1, "recurring job row must NOT be deleted"
    row = rows[0]
    # Re-armed, not terminal.
    assert row["status"] == "pending", "recurring job must return to pending, not stay failed"
    # Retry counter reset for the fresh occurrence.
    assert int(row["retry_count"]) == 0
    assert row["retry_at"] is None
    # next_run_at advanced to a FUTURE slot (the schedule survives).
    next_run = datetime.fromisoformat(row["next_run_at"])
    assert next_run > datetime.now(UTC), "next_run_at must be advanced into the future"


async def test_recurring_rearm_writes_audit_row(
    tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "stackowl.config.test_mode.TestModeGuard.assert_not_test_mode",
        lambda *_a, **_kw: None,
    )
    handler = _AlwaysFailsHandler("check_in")
    sched = _sched(tmp_db, handler)
    job = _job("check_in")
    await insert_job(tmp_db, job)

    await _exhaust_retries(tmp_db, sched, job.job_id)

    audit = await tmp_db.fetch_all(
        "SELECT event_type FROM audit_log WHERE target = ?", (job.job_id,)
    )
    assert any(
        r["event_type"] == "job_rearmed_after_failure" for r in audit
    ), "re-arm must leave an audit trail (never silent)"


async def test_one_shot_job_stays_terminal_failed(
    tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "stackowl.config.test_mode.TestModeGuard.assert_not_test_mode",
        lambda *_a, **_kw: None,
    )
    handler = _AlwaysFailsHandler("goal_execution")
    sched = _sched(tmp_db, handler)
    job = _job("goal_execution", params={"run_once": True})  # one-shot
    await insert_job(tmp_db, job)

    await _exhaust_retries(tmp_db, sched, job.job_id)

    rows = await tmp_db.fetch_all(
        "SELECT status FROM jobs WHERE job_id = ?", (job.job_id,)
    )
    assert len(rows) == 1
    assert rows[0]["status"] == "failed", "one-shot must stay terminal failed (unchanged)"
