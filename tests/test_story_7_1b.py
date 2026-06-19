"""Story 7.1 (split B) — /agents command + JobScheduler lifecycle methods."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from stackowl.commands.agent_create_command import AgentCommand
from stackowl.commands.registry import CommandRegistry
from stackowl.db.pool import DbPool
from stackowl.pipeline.state import PipelineState
from stackowl.scheduler.base import HandlerRegistry
from stackowl.scheduler.job import Job
from stackowl.scheduler.scheduler import JobScheduler
from stackowl.scheduler.scheduler_helpers import insert_job


def _state() -> PipelineState:
    return PipelineState(
        trace_id="t-1",
        session_id="s-1",
        input_text="",
        channel="cli",
        owl_name="secretary",
        pipeline_step="",
    )


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
# /agents command
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestAgentCommand:
    async def test_command_name(self) -> None:
        cmd = AgentCommand()
        assert cmd.command == "agent"

    async def test_help_when_no_subcommand(self) -> None:
        cmd = AgentCommand()
        result = await cmd.handle("", _state())
        assert "Usage:" in result

    async def test_acknowledge_resets_failure_state(self, tmp_db: DbPool) -> None:
        job = _job(
            handler="check_in",
            status="failed",
            failure_count=4,
            last_error="boom",
            enabled=False,
        )
        await insert_job(tmp_db, job)
        cmd = AgentCommand(db=tmp_db)
        result = await cmd.handle(f"acknowledge {job.job_id}", _state())
        assert "acknowledged" in result
        rows = await tmp_db.fetch_all(
            "SELECT status, failure_count, last_error, enabled FROM jobs WHERE job_id = ?",
            (job.job_id,),
        )
        assert rows[0]["status"] == "pending"
        assert rows[0]["failure_count"] == 0
        assert rows[0]["last_error"] is None
        assert int(rows[0]["enabled"]) == 1

    async def test_acknowledge_writes_audit_row(self, tmp_db: DbPool) -> None:
        job = _job(status="failed", failure_count=2)
        await insert_job(tmp_db, job)
        cmd = AgentCommand(db=tmp_db)
        await cmd.handle(f"acknowledge {job.job_id}", _state())
        audit_rows = await tmp_db.fetch_all(
            "SELECT event_type, target FROM audit_log WHERE target = ?",
            (job.job_id,),
        )
        assert any(r["event_type"] == "job_resumed" for r in audit_rows)

    async def test_acknowledge_unknown_job_reports_error(self, tmp_db: DbPool) -> None:
        cmd = AgentCommand(db=tmp_db)
        result = await cmd.handle("acknowledge no-such-job", _state())
        assert "no job" in result.lower() or "✗" in result

    async def test_create_and_register_adds_to_registry(self) -> None:
        CommandRegistry.reset()
        cmd = AgentCommand.create_and_register()
        assert CommandRegistry.instance().list() == [cmd]


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

    async def test_recover_advances_next_run_when_replay_disabled(
        self, tmp_db: DbPool
    ) -> None:
        past = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
        job = _job(next_run_at=past, replay_missed=False)
        await insert_job(tmp_db, job)
        sched = JobScheduler(db=tmp_db)
        replayed = await sched.recover(replay_window_hours=24)
        assert replayed == 0
        rows = await tmp_db.fetch_all(
            "SELECT next_run_at FROM jobs WHERE job_id = ?", (job.job_id,)
        )
        assert rows[0]["next_run_at"] > past

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
