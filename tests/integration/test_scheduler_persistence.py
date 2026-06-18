"""AgentsPersistenceIntegrationTest — Story 7.2.

Lightweight integration suite: spins up a real SQLite DB with all
migrations applied, then exercises the job_results write path that powers
``/agents log`` and the run-once delete path used by ``GoalExecutionHandler``.

The full scheduler poll loop is NOT exercised here — that is the job of
the unit suite and the dedicated scheduler tests in test_story_7_1.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import ResponseChunk
from stackowl.scheduler.handlers.goal_execution import GoalExecutionHandler
from stackowl.scheduler.job import Job
from stackowl.scheduler.scheduler_helpers import insert_job

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Test backend — a minimal :class:`OrchestratorBackend` for handler exercise.
# ---------------------------------------------------------------------------


class _StubBackend:
    """A minimal :class:`OrchestratorBackend` impl for integration testing."""

    def __init__(self, response_text: str = "ok", error: str | None = None) -> None:
        self._response_text = response_text
        self._error = error
        self.calls: list[PipelineState] = []

    async def run(self, state: PipelineState) -> PipelineState:
        self.calls.append(state)
        chunk = ResponseChunk(
            content=self._response_text,
            is_final=True,
            chunk_index=0,
            trace_id=state.trace_id,
            owl_name=state.owl_name,
        )
        return state.evolve(
            responses=(chunk,),
            errors=(self._error,) if self._error else (),
        )

    async def shutdown(self) -> None:
        return None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
async def migrated_db(tmp_path: Path) -> AsyncIterator[DbPool]:
    """A real SQLite DbPool with every migration applied."""
    db_path = tmp_path / "scheduler_integration.db"
    MigrationRunner(db_path=db_path).run()
    pool = DbPool(db_path=db_path)
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


def _make_job(
    handler: str = "goal_execution",
    *,
    params: dict[str, Any] | None = None,
) -> Job:
    job_id = f"{handler}-{uuid.uuid4().hex[:6]}"
    return Job(
        job_id=job_id,
        handler_name=handler,
        schedule="daily@09:00",
        idempotency_key=uuid.uuid4().hex,
        last_run_at=None,
        next_run_at=datetime.now(UTC).isoformat(),
        status="pending",
        params=params or {},
    )


def _disable_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "stackowl.config.test_mode.TestModeGuard.assert_not_test_mode",
        lambda *_a, **_kw: None,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSchedulerPersistence:
    async def test_migrations_create_job_results_table(self, migrated_db: DbPool) -> None:
        rows = await migrated_db.fetch_all(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='job_results'"
        )
        assert len(rows) == 1

    async def test_job_results_row_round_trip(self, migrated_db: DbPool) -> None:
        run_at = datetime.now(UTC).isoformat()
        await migrated_db.execute(
            "INSERT INTO job_results (job_id, run_at, status, result_text, duration_ms) "
            "VALUES (?, ?, ?, ?, ?)",
            ("test-job", run_at, "completed", "hello world", 42.0),
        )
        rows = await migrated_db.fetch_all(
            "SELECT job_id, status, result_text, duration_ms FROM job_results "
            "WHERE job_id = ?",
            ("test-job",),
        )
        assert len(rows) == 1
        assert rows[0]["status"] == "completed"
        assert rows[0]["result_text"] == "hello world"
        assert rows[0]["duration_ms"] == 42.0

    async def test_goal_execution_writes_job_results_row(
        self, migrated_db: DbPool, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _disable_guard(monkeypatch)
        backend = _StubBackend(response_text="weather summary here")
        handler = GoalExecutionHandler(backend=backend, db=migrated_db)  # type: ignore[arg-type]
        job = _make_job(params={"goal": "Check weather"})
        await insert_job(migrated_db, job)

        result = await handler.execute(job)

        assert result.success is True
        rows = await migrated_db.fetch_all(
            "SELECT status, result_text FROM job_results WHERE job_id = ?",
            (job.job_id,),
        )
        assert len(rows) == 1
        assert rows[0]["status"] == "completed"
        assert rows[0]["result_text"] == "weather summary here"

    async def test_goal_execution_deletes_job_when_run_once(
        self, migrated_db: DbPool, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _disable_guard(monkeypatch)
        backend = _StubBackend(response_text="one-shot done")
        handler = GoalExecutionHandler(backend=backend, db=migrated_db)  # type: ignore[arg-type]
        job = _make_job(params={"goal": "Send me a one-shot", "run_once": True})
        await insert_job(migrated_db, job)

        result = await handler.execute(job)

        assert result.success is True
        remaining = await migrated_db.fetch_all(
            "SELECT job_id FROM jobs WHERE job_id = ?", (job.job_id,)
        )
        assert remaining == []
