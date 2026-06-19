"""Story 7.2 — GoalExecutionHandler full implementation + helper unit tests.

The command-surface tests (AgentCommand, AgentCommand subcommands)
live in :mod:`tests.test_story_7_2b` so neither file crosses the B2
300-line cap.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from stackowl.commands.agent_create_helpers import (
    format_proposal,
    parse_intent_response,
    strip_code_fences,
)
from stackowl.commands.agents_helpers import format_jobs_table, format_results_table
from stackowl.commands.registry import CommandRegistry
from stackowl.exceptions import CommandParseError
from stackowl.scheduler.base import HandlerRegistry
from stackowl.scheduler.handlers.goal_execution import GoalExecutionHandler
from tests._story_7_2_helpers import (
    RecordingDb,
    StubBackend,
    disable_guard,
    make_job,
)

# ---------------------------------------------------------------------------
# A. GoalExecutionHandler — 8 tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestGoalExecutionHandler:
    async def test_handler_name(self) -> None:
        assert GoalExecutionHandler().handler_name == "goal_execution"

    async def test_execute_passes_goal_into_pipeline_state(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        disable_guard(monkeypatch)
        backend = StubBackend()
        db = RecordingDb()
        handler = GoalExecutionHandler(backend=backend, db=db)  # type: ignore[arg-type]
        job = make_job(params={"goal": "Check the weather"})

        await handler.execute(job)

        assert len(backend.calls) == 1
        assert backend.calls[0].input_text == "Check the weather"

    async def test_execute_writes_row_to_job_results(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        disable_guard(monkeypatch)
        backend = StubBackend(response_text="weather: sunny")
        db = RecordingDb()
        handler = GoalExecutionHandler(backend=backend, db=db)  # type: ignore[arg-type]
        job = make_job(params={"goal": "Check the weather"})

        await handler.execute(job)

        inserts = [e for e in db.executes if "INSERT INTO job_results" in e[0]]
        assert len(inserts) == 1
        params = inserts[0][1]
        assert params[0] == job.job_id
        assert params[2] == "completed"
        assert params[3] == "weather: sunny"

    async def test_execute_returns_success_with_response_text(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        disable_guard(monkeypatch)
        backend = StubBackend(response_text="DONE")
        db = RecordingDb()
        handler = GoalExecutionHandler(backend=backend, db=db)  # type: ignore[arg-type]
        job = make_job(params={"goal": "do it"})

        result = await handler.execute(job)
        assert result.success is True
        assert result.output == "DONE"
        assert result.metadata.get("goal") == "do it"

    async def test_execute_fails_when_goal_is_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        disable_guard(monkeypatch)
        backend = StubBackend()
        db = RecordingDb()
        handler = GoalExecutionHandler(backend=backend, db=db)  # type: ignore[arg-type]
        job = make_job(params={})

        result = await handler.execute(job)
        assert result.success is False
        assert result.error is not None
        assert backend.calls == []  # never invoked backend

    async def test_execute_deletes_job_when_run_once_true(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        disable_guard(monkeypatch)
        backend = StubBackend(response_text="ok")
        db = RecordingDb()
        handler = GoalExecutionHandler(backend=backend, db=db)  # type: ignore[arg-type]
        job = make_job(params={"goal": "ship it", "run_once": True})

        await handler.execute(job)

        deletes = [e for e in db.executes if "DELETE FROM jobs" in e[0]]
        assert len(deletes) == 1
        assert deletes[0][1] == (job.job_id,)

    async def test_execute_does_not_delete_when_run_once_false(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        disable_guard(monkeypatch)
        backend = StubBackend(response_text="ok")
        db = RecordingDb()
        handler = GoalExecutionHandler(backend=backend, db=db)  # type: ignore[arg-type]
        job = make_job(params={"goal": "recurring task", "run_once": False})

        await handler.execute(job)
        deletes = [e for e in db.executes if "DELETE FROM jobs" in e[0]]
        assert deletes == []

    async def test_execute_calls_test_mode_guard(self) -> None:
        from stackowl.config.test_mode import TestModeGuard

        TestModeGuard.activate()
        try:
            handler = GoalExecutionHandler(backend=StubBackend(), db=RecordingDb())  # type: ignore[arg-type]
            with pytest.raises(Exception) as excinfo:
                await handler.execute(make_job(params={"goal": "x"}))
            assert "test mode" in str(excinfo.value).lower()
        finally:
            TestModeGuard.deactivate()


# ---------------------------------------------------------------------------
# D. Prompt template + helper unit tests
# ---------------------------------------------------------------------------


class TestPromptTemplate:
    def test_agent_intent_template_exists(self) -> None:
        path = (
            Path(__file__).resolve().parent.parent
            / "src/stackowl/scheduler/prompts/agent_intent.j2"
        )
        assert path.exists()
        text = path.read_text(encoding="utf-8")
        assert "user_intent" in text
        assert "handler_name" in text


class TestAgentCreateHelpers:
    def test_parse_intent_response_strips_code_fences(self) -> None:
        payload = (
            "```json\n"
            '{"handler_name": "goal_execution", "schedule": "daily@09:00", '
            '"params": {"goal": "do x"}, "primary_channel": null}\n'
            "```"
        )
        parsed = parse_intent_response(payload)
        assert parsed["handler_name"] == "goal_execution"
        assert parsed["schedule"] == "daily@09:00"

    def test_parse_intent_response_rejects_unknown_handler(self) -> None:
        payload = json.dumps(
            {"handler_name": "explode", "schedule": "daily@09:00", "params": {}}
        )
        with pytest.raises(CommandParseError):
            parse_intent_response(payload)

    def test_parse_intent_response_rejects_missing_schedule(self) -> None:
        payload = json.dumps(
            {"handler_name": "goal_execution", "schedule": "", "params": {}}
        )
        with pytest.raises(CommandParseError):
            parse_intent_response(payload)

    def test_format_proposal_includes_all_fields(self) -> None:
        proposal = {
            "handler_name": "goal_execution",
            "schedule": "daily@09:00",
            "params": {"goal": "make me coffee"},
            "primary_channel": None,
        }
        rendered = format_proposal(proposal)
        assert "goal_execution" in rendered
        assert "daily@09:00" in rendered
        assert "make me coffee" in rendered

    def test_strip_code_fences_handles_plain_text(self) -> None:
        assert strip_code_fences("no fences") == "no fences"


class TestAgentsHelpers:
    def test_format_jobs_table_empty(self) -> None:
        out = format_jobs_table([])
        assert "(no background agents" in out

    def test_format_jobs_table_with_rows(self) -> None:
        jobs = [make_job("goal_execution"), make_job("morning_brief")]
        out = format_jobs_table(jobs)
        assert "goal_execution" in out
        assert "morning_brief" in out

    def test_format_results_table_empty(self) -> None:
        out = format_results_table("foo", [])
        assert "No runs recorded" in out
        assert "foo" in out


# ---------------------------------------------------------------------------
# Teardown — reset shared singletons (mirrored in test_story_7_2b).
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_singletons() -> AsyncIterator[None]:  # type: ignore[misc]
    HandlerRegistry.reset()
    CommandRegistry.reset()
    yield  # type: ignore[misc]
    HandlerRegistry.reset()
    CommandRegistry.reset()
