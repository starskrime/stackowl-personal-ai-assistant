"""Regression tests for scheduler mutations — MAJOR-2 (run_now) + NIT-2 (params).

``run_now`` must (a) refuse paused/disabled jobs without invoking the handler,
(b) win-or-lose the same pending→running compare-and-swap the poller uses so an
out-of-band run and a concurrent poll tick can never double-dispatch, and
(c) write a ``job_runs`` row + restore a recurring job to its next slot.
``update_job`` must never let a caller rewrite the ``owl``/``created_by``
ownership tags via the params merge (NIT-2).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.scheduler.base import HandlerRegistry, JobHandler
from stackowl.scheduler.job import Job, JobResult
from stackowl.scheduler.scheduler import JobScheduler

pytestmark = pytest.mark.asyncio

# America/New_York — any non-UTC zone works; picked to match test_daily_tz.py.
_NY = "America/New_York"


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


async def _seed_job(
    db: DbPool, *, schedule: str = "every 30m", status: str = "pending", enabled: int = 1
) -> str:
    sched = JobScheduler(db=db)
    job = await sched.create_job(
        handler_name="goal_execution",
        schedule=schedule,
        params={"goal": "g", "created_by": "cronjob", "owl": "scout"},
    )
    await db.execute(
        "UPDATE jobs SET status = ?, enabled = ? WHERE job_id = ?",
        (status, enabled, job.job_id),
    )
    return job.job_id


# --------------------------------------------------------------------------- MAJOR-2


async def test_run_now_rejects_paused_job_handler_not_called(migrated_db: DbPool) -> None:
    handler = _CountingHandler()
    HandlerRegistry.instance().register(handler)
    # A paused job: pause() sets status='failed', enabled=0.
    job_id = await _seed_job(migrated_db)
    await JobScheduler(db=migrated_db).pause(job_id)

    result = await JobScheduler(db=migrated_db).run_now(job_id)

    assert result is not None
    assert result.success is False
    assert result.error is not None and "paused" in result.error
    assert handler.runs == [], "a paused/disabled job must never invoke its handler"
    # Still paused — run_now did not silently re-enable it.
    rows = await migrated_db.fetch_all(
        "SELECT enabled FROM jobs WHERE job_id = ?", (job_id,)
    )
    assert rows[0]["enabled"] in (0, False)


async def test_run_now_loses_transition_when_already_running(migrated_db: DbPool) -> None:
    handler = _CountingHandler()
    HandlerRegistry.instance().register(handler)
    # Simulate the poller already in-flight: status already 'running'.
    job_id = await _seed_job(migrated_db, status="running")

    result = await JobScheduler(db=migrated_db).run_now(job_id)

    assert result is not None
    assert result.success is False
    assert result.error is not None and "not runnable" in result.error
    assert handler.runs == [], "must not double-dispatch a job already 'running'"


async def test_run_now_wins_transition_runs_and_records(migrated_db: DbPool) -> None:
    handler = _CountingHandler()
    HandlerRegistry.instance().register(handler)
    job_id = await _seed_job(migrated_db, schedule="every 30m", status="pending")

    result = await JobScheduler(db=migrated_db).run_now(job_id)

    assert result is not None and result.success is True
    assert handler.runs == [job_id]
    # A job_runs row was written (the bug: run_now wrote none).
    runs = await migrated_db.fetch_all(
        "SELECT status FROM job_runs WHERE job_id = ?", (job_id,)
    )
    assert len(runs) == 1
    assert runs[0]["status"] == "completed"
    # Recurring job restored to pending for the next slot (not stuck 'running').
    jobrow = await migrated_db.fetch_all(
        "SELECT status FROM jobs WHERE job_id = ?", (job_id,)
    )
    assert jobrow[0]["status"] == "pending"


async def test_run_now_unknown_job_returns_none(migrated_db: DbPool) -> None:
    assert await JobScheduler(db=migrated_db).run_now("goal_execution-deadbeef") is None


# --------------------------------------------------------------------------- FIX 3 (stale running)


async def test_recover_reaps_stale_running_job(migrated_db: DbPool) -> None:
    """A job left ``status='running'`` by a crashed process is reset to pending.

    At startup the process that set ``running`` is gone, so the job is stale and
    would otherwise wedge forever (the poller/recover only select 'pending').
    ``recover()`` must reap it back to a runnable state.
    """
    handler = _CountingHandler()
    HandlerRegistry.instance().register(handler)
    # A recurring job wedged in 'running' (simulating a crashed run_now/_run_job).
    job_id = await _seed_job(migrated_db, schedule="every 5m", status="running")

    await JobScheduler(db=migrated_db).recover()

    rows = await migrated_db.fetch_all(
        "SELECT status, next_run_at FROM jobs WHERE job_id = ?", (job_id,)
    )
    assert rows[0]["status"] == "pending", "stale 'running' job must be reaped to 'pending'"
    assert rows[0]["next_run_at"] is not None


async def test_recover_reap_is_idempotent_on_clean_db(migrated_db: DbPool) -> None:
    """No 'running' rows → recover reaps nothing and leaves pending jobs alone."""
    handler = _CountingHandler()
    HandlerRegistry.instance().register(handler)
    job_id = await _seed_job(migrated_db, schedule="every 5m", status="pending")

    await JobScheduler(db=migrated_db).recover()

    rows = await migrated_db.fetch_all(
        "SELECT status FROM jobs WHERE job_id = ?", (job_id,)
    )
    assert rows[0]["status"] == "pending"


# --------------------------------------------------------------------------- NIT-2


# --------------------------------------------------------------------------- tz propagation
# The bug: update_job / run_now's restore / recover's reap each called
# compute_next_run without the scheduler's configured tz, silently re-arming a
# daily@HH:MM job in UTC. JobScheduler.update_job/run_now are thin delegates
# that now thread self._tz through — these prove the delegate, not
# compute_next_run's own tz arithmetic (already covered by test_daily_tz.py).


async def test_update_job_recomputes_next_run_in_configured_tz(migrated_db: DbPool) -> None:
    job_id = await _seed_job(migrated_db, schedule="daily@08:00")
    sched = JobScheduler(db=migrated_db, tz=_NY)

    updated = await sched.update_job(job_id, schedule="daily@09:00")

    assert updated is not None
    local = datetime.fromisoformat(updated.next_run_at).astimezone(ZoneInfo(_NY))
    assert (local.hour, local.minute) == (9, 0)


async def test_run_now_restores_next_run_in_configured_tz(migrated_db: DbPool) -> None:
    handler = _CountingHandler()
    HandlerRegistry.instance().register(handler)
    job_id = await _seed_job(migrated_db, schedule="daily@08:00", status="pending")
    sched = JobScheduler(db=migrated_db, tz=_NY)

    await sched.run_now(job_id)

    rows = await migrated_db.fetch_all(
        "SELECT next_run_at FROM jobs WHERE job_id = ?", (job_id,)
    )
    local = datetime.fromisoformat(rows[0]["next_run_at"]).astimezone(ZoneInfo(_NY))
    assert (local.hour, local.minute) == (8, 0)


async def test_recover_reaps_stale_running_in_configured_tz(migrated_db: DbPool) -> None:
    handler = _CountingHandler()
    HandlerRegistry.instance().register(handler)
    job_id = await _seed_job(migrated_db, schedule="daily@08:00", status="running")
    sched = JobScheduler(db=migrated_db, tz=_NY)

    await sched.recover()

    rows = await migrated_db.fetch_all(
        "SELECT next_run_at FROM jobs WHERE job_id = ?", (job_id,)
    )
    local = datetime.fromisoformat(rows[0]["next_run_at"]).astimezone(ZoneInfo(_NY))
    assert (local.hour, local.minute) == (8, 0)


async def test_update_job_cannot_rewrite_ownership_tags(migrated_db: DbPool) -> None:
    job_id = await _seed_job(migrated_db)
    # A caller tries to smuggle new ownership tags through the params merge.
    updated = await JobScheduler(db=migrated_db).update_job(
        job_id,
        goal="new goal",
        params={"owl": "attacker", "created_by": "not_cronjob", "extra": "kept"},
    )
    assert updated is not None
    # Ownership tags are preserved; only the non-protected key is merged.
    assert updated.params["owl"] == "scout"
    assert updated.params["created_by"] == "cronjob"
    assert updated.params["goal"] == "new goal"
    assert updated.params["extra"] == "kept"
