"""Story 7.1 (split B) — JobScheduler lifecycle methods.

The command-surface tests (formerly ``/agents``/``AgentCommand``) were
retired in Task 7 along with the command they covered.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from stackowl.commands.registry import CommandRegistry
from stackowl.db.pool import DbPool
from stackowl.scheduler.base import HandlerRegistry
from stackowl.scheduler.job import Job
from stackowl.scheduler.scheduler import JobScheduler
from stackowl.scheduler.scheduler_helpers import insert_job


def _job(handler: str = "check_in", **overrides: Any) -> Job:
    defaults: dict[str, Any] = dict(
        job_id=f"job-{uuid.uuid4().hex[:6]}",
        handler_name=handler,
        schedule="daily@09:00",
        idempotency_key=uuid.uuid4().hex,
        last_run_at=None,
        next_run_at=datetime.now(UTC).isoformat(),
        status="pending",
    )
    defaults.update(overrides)
    return Job(**defaults)


@pytest.fixture(autouse=True)
def _reset_singletons() -> Any:
    HandlerRegistry.reset()
    CommandRegistry.reset()
    yield
    HandlerRegistry.reset()
    CommandRegistry.reset()


# ---------------------------------------------------------------------------
# JobScheduler lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestSchedulerLifecycle:
    async def test_pause_sets_failed_and_disabled(self, tmp_db: DbPool) -> None:
        job = _job(status="pending")
        await insert_job(tmp_db, job)
        sched = JobScheduler(db=tmp_db)
        await sched.pause(job.job_id)
        rows = await tmp_db.fetch_all(
            "SELECT status, enabled FROM jobs WHERE job_id = ?", (job.job_id,)
        )
        assert rows[0]["status"] == "failed"
        assert int(rows[0]["enabled"]) == 0

    async def test_resume_clears_failure_state(self, tmp_db: DbPool) -> None:
        job = _job(
            status="failed",
            failure_count=3,
            last_error="timeout",
            enabled=False,
        )
        await insert_job(tmp_db, job)
        sched = JobScheduler(db=tmp_db)
        await sched.resume(job.job_id)
        rows = await tmp_db.fetch_all(
            "SELECT status, failure_count, last_error, enabled FROM jobs WHERE job_id = ?",
            (job.job_id,),
        )
        assert rows[0]["status"] == "pending"
        assert rows[0]["failure_count"] == 0
        assert rows[0]["last_error"] is None
        assert int(rows[0]["enabled"]) == 1

    async def test_stop_job_removes_row(self, tmp_db: DbPool) -> None:
        job = _job()
        await insert_job(tmp_db, job)
        sched = JobScheduler(db=tmp_db)
        await sched.stop_job(job.job_id)
        rows = await tmp_db.fetch_all(
            "SELECT job_id FROM jobs WHERE job_id = ?", (job.job_id,)
        )
        assert rows == []

    async def test_recover_advances_a_FREQUENT_job_that_has_moved_on(
        self, tmp_db: DbPool
    ) -> None:
        """The contract this test has always protected, on a job it still holds for.

        IT USED TO SAY `..._when_replay_disabled`, and that name stated a rule
        DEBT-228 retired: `replay_missed=False` no longer means "do not replay",
        it means "no explicit override — the schedule decides". The default
        `_job()` schedule is `daily@09:00`, so under the new rule this fixture
        REPLAYS, which is the `check_in` case the fix exists for. The assertion
        was correct about the mechanism and wrong about the example.

        `every 30m` keeps the original meaning: overdue by two hours is four slots
        stale, the work is fungible across them, and advancing loses nothing.
        """
        past = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
        job = _job(schedule="every 30m", next_run_at=past, replay_missed=False)
        await insert_job(tmp_db, job)
        sched = JobScheduler(db=tmp_db)
        replayed = await sched.recover(replay_window_hours=24)
        assert replayed == 0
        rows = await tmp_db.fetch_all(
            "SELECT next_run_at FROM jobs WHERE job_id = ?", (job.job_id,)
        )
        assert rows[0]["next_run_at"] > past

    async def test_recover_replays_a_DAILY_job_the_flag_never_claimed(
        self, tmp_db: DbPool
    ) -> None:
        """The half the old test asserted BACKWARDS, kept here so this file
        records the contract change rather than quietly dropping it.

        A `daily@` job overdue by two hours with no flag set is the live
        `check_in` case: dropping the slot costs the whole day, so the schedule's
        own period earns the replay without anything being remembered at the
        creation site.
        """
        past = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
        job = _job(next_run_at=past, replay_missed=False)   # daily@09:00
        await insert_job(tmp_db, job)
        sched = JobScheduler(db=tmp_db)
        assert await sched.recover(replay_window_hours=24) == 1

    async def test_create_job_inserts_row(self, tmp_db: DbPool) -> None:
        sched = JobScheduler(db=tmp_db)
        job = await sched.create_job(
            handler_name="check_in",
            schedule="daily@08:00",
            params={"k": "v"},
        )
        rows = await tmp_db.fetch_all(
            "SELECT handler_name, schedule, params FROM jobs WHERE job_id = ?",
            (job.job_id,),
        )
        assert rows[0]["handler_name"] == "check_in"
        assert rows[0]["schedule"] == "daily@08:00"
        assert '"k"' in (rows[0]["params"] or "")

    async def test_list_jobs_returns_inserted_rows(self, tmp_db: DbPool) -> None:
        await insert_job(tmp_db, _job(handler="check_in"))
        await insert_job(tmp_db, _job(handler="tool_pruning"))
        sched = JobScheduler(db=tmp_db)
        jobs = await sched.list_jobs()
        names = sorted(j.handler_name for j in jobs)
        assert "check_in" in names
        assert "tool_pruning" in names
