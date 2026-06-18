"""Shared test helpers for Story 7.2 — used by test_story_7_2 and test_story_7_2b.

Kept in a non-``test_`` module so pytest doesn't try to collect tests here
and so neither test file is forced over the B2 300-line cap.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import ResponseChunk
from stackowl.scheduler.job import Job


def disable_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable :class:`TestModeGuard` for a single test."""
    monkeypatch.setattr(
        "stackowl.config.test_mode.TestModeGuard.assert_not_test_mode",
        lambda *_a, **_kw: None,
    )


def make_job(
    handler: str = "goal_execution",
    *,
    params: dict[str, Any] | None = None,
    **overrides: Any,
) -> Job:
    """Construct a :class:`Job` with sensible defaults."""
    defaults: dict[str, Any] = dict(
        job_id=f"job-{uuid.uuid4().hex[:6]}",
        handler_name=handler,
        schedule="daily@09:00",
        idempotency_key=uuid.uuid4().hex,
        last_run_at=None,
        next_run_at=datetime.now(UTC).isoformat(),
        status="pending",
        params=params or {},
    )
    defaults.update(overrides)
    return Job(**defaults)


def make_state(session_id: str = "test-session") -> PipelineState:
    """Return a minimal :class:`PipelineState` for command-surface tests."""
    return PipelineState(
        trace_id="trace-test",
        session_id=session_id,
        input_text="",
        channel="cli",
        owl_name="secretary",
        pipeline_step="",
    )


class StubBackend:
    """Minimal :class:`OrchestratorBackend` for goal-execution tests."""

    def __init__(self, response_text: str = "ok", errors: tuple[str, ...] = ()) -> None:
        self._response_text = response_text
        self._errors = errors
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
        return state.evolve(responses=(chunk,), errors=self._errors)

    async def shutdown(self) -> None:
        return None


class RecordingDb:
    """Captures ``execute`` / ``fetch_all`` calls — no real DB underneath."""

    def __init__(self, fetch_returns: list[dict[str, Any]] | None = None) -> None:
        self.executes: list[tuple[str, tuple[Any, ...]]] = []
        self.fetches: list[tuple[str, tuple[Any, ...]]] = []
        self._fetch_returns = fetch_returns or []

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        self.executes.append((sql, tuple(params)))

    async def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        self.fetches.append((sql, tuple(params)))
        return list(self._fetch_returns)
