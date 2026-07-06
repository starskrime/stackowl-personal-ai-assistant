"""Story 7.2 — GoalExecutionHandler full implementation.

The command-surface tests (AgentCommand + its helpers) were retired in
Task 7 along with the ``/agent`` command they covered — the scheduler
handler below is the surviving, reused piece.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from stackowl.commands.registry import CommandRegistry
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
# D. Prompt template
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


# ---------------------------------------------------------------------------
# Teardown — reset shared singletons.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_singletons() -> AsyncIterator[None]:  # type: ignore[misc]
    HandlerRegistry.reset()
    CommandRegistry.reset()
    yield  # type: ignore[misc]
    HandlerRegistry.reset()
    CommandRegistry.reset()
