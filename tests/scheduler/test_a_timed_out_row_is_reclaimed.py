"""A handler that outruns its timeout must not strand its row forever.

MEASURED 2026-08-25. `incident_escalation` was due at 22:56:18, the scheduler
logged "handler timed out — freed for retry/re-arm" at 23:16:19, and at 23:27:21
the row was STILL status='running' — while the handler was STILL working, logging
"RCA complete" and "incident opened" at 23:25:15. Thirty-one minutes into a run
that timed out at twenty.

TWO DEFECTS, AND THE SECOND IS THE ONE THAT BITES.

  THE LOG LIED. "freed for retry/re-arm" is what a reader uses to conclude the
  job recovered — I concluded exactly that, an hour after writing the
  measurement. Nothing was freed and nothing was stopped. `asyncio.wait_for`
  cancels the awaited coroutine, not tasks that coroutine spawned with
  create_task, and incident RCA spawns sub-agent turns.

  THE ROW WAS UNRECLAIMABLE. The CAS claim is
  ``UPDATE jobs SET status='running' WHERE status='pending'``, so a row left
  'running' can never be picked up again. `reap_stale_running` exists, and its
  docstring says it "Runs at startup (from recover())" — startup ONLY. So the job
  was dead until the next process restart, and the job in question is the
  self-healing RCA loop.

WHY THE STARTUP REAPER CANNOT SIMPLY BE RUN ON A TIMER. Its safety rests on a
sentence in its own docstring: "the process that set a job ``running`` is, by
definition, gone, so ANY ``running`` row is stale." That is true at startup and
FALSE mid-life — most running rows are jobs running right now. A periodic reaper
therefore needs a staleness test, and this one uses the only timestamp available
without a migration: a row DUE at T that is still running well past
T + handler-timeout has outlived the scheduler's own patience.

Bakir chose this over freeing the row the moment the timeout fires, because that
would guarantee two concurrent runs of the same handler — for incident RCA, that
means duplicate incidents. The margin is what buys the difference: at 2x the
timeout the old work has almost certainly finished or is genuinely abandoned.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from stackowl.db.pool import DbPool
from stackowl.scheduler.scheduler_helpers import (
    _STALE_RUNNING_AFTER_SEC,
    insert_job,
    reap_timed_out_running,
)
from stackowl.scheduler.job import Job

pytestmark = pytest.mark.asyncio


def _job(handler: str, *, due_minutes_ago: float, status: str) -> Job:
    due = datetime.now(UTC) - timedelta(minutes=due_minutes_ago)
    return Job(
        job_id=f"{handler}-{uuid.uuid4().hex[:6]}",
        handler_name=handler,
        schedule="every 10 minutes",
        idempotency_key=uuid.uuid4().hex,
        last_run_at=None,
        next_run_at=due.isoformat(),
        status=status,
        params={},
    )


async def _status(db: DbPool, job_id: str) -> str:
    rows = await db.fetch_all("SELECT status FROM jobs WHERE job_id = ?", (job_id,))
    return str(rows[0]["status"])


async def test_a_row_running_far_past_its_timeout_is_reclaimed(tmp_db: DbPool) -> None:
    """THE defect. incident_escalation sat 'running' with next_run_at 29 minutes
    in the past and nothing could ever claim it again."""
    stale_minutes = (_STALE_RUNNING_AFTER_SEC / 60.0) + 5
    job = _job("incident_escalation", due_minutes_ago=stale_minutes, status="running")
    await insert_job(tmp_db, job)

    reaped = await reap_timed_out_running(tmp_db)

    assert reaped == 1
    assert await _status(tmp_db, job.job_id) == "pending", (
        "a row abandoned past the handler timeout must become claimable again — "
        "the CAS claim only ever selects status='pending'"
    )


async def test_a_job_RUNNING_NORMALLY_is_left_alone(tmp_db: DbPool) -> None:
    """The whole reason this is not just reap_stale_running on a timer. Most
    running rows are jobs running RIGHT NOW; reaping those would let the same
    handler run twice, which for incident RCA means duplicate incidents."""
    job = _job("incident_escalation", due_minutes_ago=1, status="running")
    await insert_job(tmp_db, job)

    reaped = await reap_timed_out_running(tmp_db)

    assert reaped == 0
    assert await _status(tmp_db, job.job_id) == "running"


async def test_a_job_still_within_its_timeout_is_left_alone(tmp_db: DbPool) -> None:
    """A slow handler is not an abandoned one. Below the threshold it keeps its
    claim, because the scheduler has not yet given up on it either."""
    just_under = (_STALE_RUNNING_AFTER_SEC / 60.0) - 5
    job = _job("dream_worker", due_minutes_ago=just_under, status="running")
    await insert_job(tmp_db, job)

    assert await reap_timed_out_running(tmp_db) == 0
    assert await _status(tmp_db, job.job_id) == "running"


async def test_pending_rows_are_never_touched(tmp_db: DbPool) -> None:
    """It reclaims claims, not schedules. A long-overdue PENDING row is the
    scheduler's ordinary backlog and must keep its due time."""
    job = _job("retry_sweep", due_minutes_ago=600, status="pending")
    await insert_job(tmp_db, job)

    assert await reap_timed_out_running(tmp_db) == 0
    assert await _status(tmp_db, job.job_id) == "pending"


async def test_a_clean_table_reaps_nothing(tmp_db: DbPool) -> None:
    """Idempotent, and quiet when there is nothing to say."""
    assert await reap_timed_out_running(tmp_db) == 0


async def test_the_margin_is_wider_than_the_handler_timeout(tmp_db: Any) -> None:
    """The margin IS the safety. Reaping at exactly the timeout would free a row
    whose work is definitely still running — the option Bakir rejected because it
    guarantees two concurrent runs of the same handler."""
    from stackowl.scheduler.scheduler import _HANDLER_TIMEOUT_SEC

    assert _STALE_RUNNING_AFTER_SEC > _HANDLER_TIMEOUT_SEC, (
        "a row must be reclaimed only once it has outlived the scheduler's own "
        "patience by a clear margin, not the instant the timeout fires"
    )
