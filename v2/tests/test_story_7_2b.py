"""Story 7.2 — command-surface tests (AgentCreateCommand + AgentsCommand).

Split from :mod:`tests.test_story_7_2` to keep both test files inside the
B2 300-line cap.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest

from stackowl.commands.agent_create_command import AgentCreateCommand
from stackowl.commands.agents_command import AgentsCommand
from stackowl.commands.registry import CommandRegistry
from stackowl.scheduler.base import HandlerRegistry
from stackowl.scheduler.job import Job
from tests._story_7_2_helpers import RecordingDb, make_job, make_state

# ---------------------------------------------------------------------------
# Local stubs
# ---------------------------------------------------------------------------


class _StubScheduler:
    """Stand-in :class:`JobScheduler` with recording surface for command tests."""

    def __init__(self) -> None:
        self.paused: list[str] = []
        self.resumed: list[str] = []
        self.stopped: list[str] = []
        self.created: list[dict[str, Any]] = []
        self._list_returns: list[Job] = []

    def with_jobs(self, jobs: list[Job]) -> _StubScheduler:
        self._list_returns = jobs
        return self

    async def pause(self, job_id: str) -> None:
        self.paused.append(job_id)

    async def resume(self, job_id: str) -> None:
        self.resumed.append(job_id)

    async def stop_job(self, job_id: str) -> None:
        self.stopped.append(job_id)

    async def list_jobs(self) -> list[Job]:
        return list(self._list_returns)

    async def create_job(
        self,
        *,
        handler_name: str,
        schedule: str,
        idempotency_key: str | None = None,
        params: dict[str, object] | None = None,
        replay_missed: bool = False,
        primary_channel: str | None = None,
    ) -> Job:
        record = {
            "handler_name": handler_name,
            "schedule": schedule,
            "params": dict(params or {}),
            "primary_channel": primary_channel,
        }
        self.created.append(record)
        job_id = f"{handler_name}-{uuid.uuid4().hex[:6]}"
        return Job(
            job_id=job_id,
            handler_name=handler_name,
            schedule=schedule,
            idempotency_key=idempotency_key or f"{handler_name}:{job_id}",
            last_run_at=None,
            next_run_at="2026-05-24T09:00:00+00:00",
            status="pending",
            params=dict(params or {}),
            replay_missed=replay_missed,
            primary_channel=primary_channel,
        )


class _StubProviderRegistry:
    """Returns a single canned provider via ``get_by_tier``."""

    def __init__(self, canned_text: str) -> None:
        from stackowl.providers.mock_provider import MockProvider

        self._provider = MockProvider(name="stub-fast", canned_text=canned_text)
        self.get_by_tier_calls: list[str] = []

    def get_by_tier(self, tier: str) -> Any:
        self.get_by_tier_calls.append(tier)
        return self._provider


# ---------------------------------------------------------------------------
# B. AgentCreateCommand — 5 tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestAgentCreateCommand:
    async def test_command_name_is_agent(self) -> None:
        cmd = AgentCreateCommand()
        assert cmd.command == "agent"

    async def test_create_calls_llm_and_returns_proposal(self) -> None:
        canned = json.dumps(
            {
                "handler_name": "goal_execution",
                "schedule": "daily@09:00",
                "params": {"goal": "Brief me each morning"},
                "primary_channel": None,
            }
        )
        provider_registry = _StubProviderRegistry(canned_text=canned)
        scheduler = _StubScheduler()
        cmd = AgentCreateCommand(
            scheduler=scheduler,  # type: ignore[arg-type]
            provider_registry=provider_registry,  # type: ignore[arg-type]
        )

        out = await cmd.handle("create Brief me each morning", make_state())

        assert provider_registry.get_by_tier_calls == ["fast"]
        assert "Proposed agent" in out
        assert "daily@09:00" in out
        assert scheduler.created == []  # NOT yet created

    async def test_confirm_creates_job_after_proposal(self) -> None:
        canned = json.dumps(
            {
                "handler_name": "goal_execution",
                "schedule": "daily@09:00",
                "params": {"goal": "ship a v2"},
                "primary_channel": None,
            }
        )
        provider_registry = _StubProviderRegistry(canned_text=canned)
        scheduler = _StubScheduler()
        cmd = AgentCreateCommand(
            scheduler=scheduler,  # type: ignore[arg-type]
            provider_registry=provider_registry,  # type: ignore[arg-type]
        )
        state = make_state("sess-1")
        await cmd.handle("create ship a v2", state)

        out = await cmd.handle("confirm", state)

        assert len(scheduler.created) == 1
        assert scheduler.created[0]["handler_name"] == "goal_execution"
        assert scheduler.created[0]["params"] == {"goal": "ship a v2"}
        assert "created" in out.lower()

    async def test_cancel_discards_pending_proposal(self) -> None:
        canned = json.dumps(
            {
                "handler_name": "goal_execution",
                "schedule": "daily@09:00",
                "params": {"goal": "drop me"},
                "primary_channel": None,
            }
        )
        provider_registry = _StubProviderRegistry(canned_text=canned)
        scheduler = _StubScheduler()
        cmd = AgentCreateCommand(
            scheduler=scheduler,  # type: ignore[arg-type]
            provider_registry=provider_registry,  # type: ignore[arg-type]
        )
        state = make_state("sess-cancel")
        await cmd.handle("create drop me", state)

        out_cancel = await cmd.handle("cancel", state)
        out_confirm = await cmd.handle("confirm", state)

        assert "discarded" in out_cancel.lower()
        assert scheduler.created == []
        # After cancel, confirm has nothing to do.
        assert "no pending" in out_confirm.lower()

    async def test_confirm_with_no_pending_returns_no_pending_message(self) -> None:
        scheduler = _StubScheduler()
        provider_registry = _StubProviderRegistry(canned_text="{}")
        cmd = AgentCreateCommand(
            scheduler=scheduler,  # type: ignore[arg-type]
            provider_registry=provider_registry,  # type: ignore[arg-type]
        )

        out = await cmd.handle("confirm", make_state("empty-sess"))
        assert "no pending" in out.lower()


# ---------------------------------------------------------------------------
# C. AgentsCommand subcommands — 7 tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestAgentsCommandSubcommands:
    async def test_list_calls_scheduler_and_formats_table(self) -> None:
        jobs = [make_job("goal_execution"), make_job("morning_brief")]
        scheduler = _StubScheduler().with_jobs(jobs)
        cmd = AgentsCommand(scheduler=scheduler)  # type: ignore[arg-type]

        out = await cmd.handle("list", make_state())
        assert "handler" in out
        assert "goal_execution" in out
        assert "morning_brief" in out

    async def test_pause_calls_scheduler_pause(self) -> None:
        scheduler = _StubScheduler()
        cmd = AgentsCommand(scheduler=scheduler)  # type: ignore[arg-type]

        out = await cmd.handle("pause job-123", make_state())
        assert scheduler.paused == ["job-123"]
        assert "paused" in out.lower()

    async def test_resume_calls_scheduler_resume(self) -> None:
        scheduler = _StubScheduler()
        cmd = AgentsCommand(scheduler=scheduler)  # type: ignore[arg-type]

        out = await cmd.handle("resume job-456", make_state())
        assert scheduler.resumed == ["job-456"]
        assert "resumed" in out.lower()

    async def test_stop_prompts_confirmation_without_yes(self) -> None:
        scheduler = _StubScheduler()
        cmd = AgentsCommand(scheduler=scheduler)  # type: ignore[arg-type]

        out = await cmd.handle("stop job-789", make_state())
        assert scheduler.stopped == []
        assert "YES" in out
        assert "stop" in out.lower()

    async def test_stop_with_yes_calls_scheduler_stop_job(self) -> None:
        scheduler = _StubScheduler()
        cmd = AgentsCommand(scheduler=scheduler)  # type: ignore[arg-type]

        out = await cmd.handle("stop job-789 YES", make_state())
        assert scheduler.stopped == ["job-789"]
        assert "stopped" in out.lower()

    async def test_log_shows_last_runs(self) -> None:
        rows = [
            {
                "run_at": "2026-05-23T10:00:00+00:00",
                "status": "completed",
                "result_text": "result one",
                "duration_ms": 12.5,
            },
            {
                "run_at": "2026-05-23T09:00:00+00:00",
                "status": "failed",
                "result_text": "error context",
                "duration_ms": 5.0,
            },
        ]
        db = RecordingDb(fetch_returns=rows)
        cmd = AgentsCommand(db=db)  # type: ignore[arg-type]

        out = await cmd.handle("log job-abc", make_state())
        assert "result one" in out
        assert "completed" in out
        assert "failed" in out

    async def test_log_returns_no_runs_when_empty(self) -> None:
        db = RecordingDb(fetch_returns=[])
        cmd = AgentsCommand(db=db)  # type: ignore[arg-type]
        out = await cmd.handle("log job-xyz", make_state())
        assert "No runs recorded" in out
        assert "job-xyz" in out


# ---------------------------------------------------------------------------
# Teardown
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_singletons() -> AsyncIterator[None]:  # type: ignore[misc]
    HandlerRegistry.reset()
    CommandRegistry.reset()
    yield  # type: ignore[misc]
    HandlerRegistry.reset()
    CommandRegistry.reset()
