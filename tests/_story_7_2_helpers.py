"""Shared test helpers for Story 7.2 — used by test_story_7_2 and test_story_7_2b.

Kept in a non-``test_`` module so pytest doesn't try to collect tests here
and so neither test file is forced over the B2 300-line cap.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
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


def make_state(session_key: str = "test-session") -> PipelineState:
    """Return a minimal :class:`PipelineState` for command-surface tests."""
    return PipelineState(
        trace_id="trace-test",
        session_key=session_key,
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


def stub_submit_command(
    monkeypatch: pytest.MonkeyPatch,
    *,
    rollup: str | None = "delivered",
    success: bool | None = None,
    error: str | None = None,
    result: dict[str, Any] | None = None,
    on_call: Callable[[dict[str, Any]], None] | None = None,
) -> list[dict[str, Any]]:
    """Story 4.8 — stub ``commands/spec/submit.py::submit_command`` for a
    handler-level test that wants to script the OUTCOME a submitted delivery
    command reports, without driving the full action-policy gate or a real
    ``ProactiveDeliverer``/``DbPool`` (morning_brief/check_in/goal_execution/
    urgent_command all call ``submit_command`` via a deferred, call-time
    ``from stackowl.commands.spec.submit import submit_command`` -- patching
    the SOURCE module's attribute is what makes that late-bound import pick
    up the fake).

    Captures every call's ``(command_type, payload, kwargs)`` in the returned
    list, so a test can assert on exactly what a handler submitted (message/
    category/urgency/channels) — the same thing the old direct-deliverer
    fakes let a test inspect, one layer up the new one-door seam.

    ``rollup``/``result`` — mutually intended for different callers:
    ``rollup`` is the shorthand the 3 job-scoped delivery translators
    (``outcome_from_submission``) read; ``result`` is the raw dict for a
    caller (e.g. ``UrgentCommand``) that reads different keys
    (``delivered``/``failed``/``total``). Passing ``result`` overrides
    ``rollup`` entirely.
    """
    from stackowl.commands.spec.context import CommandOutcome
    from stackowl.commands.spec.submit import CommandSubmission

    calls: list[dict[str, Any]] = []
    resolved_result = (
        dict(result) if result is not None
        else ({"rollup": rollup} if rollup is not None else {})
    )
    resolved_success = success if success is not None else True

    async def _fake(db: Any, command_type: str, payload: Any, **kwargs: Any) -> CommandSubmission:
        record = {"command_type": command_type, "payload": payload, "kwargs": kwargs}
        calls.append(record)
        if on_call is not None:
            on_call(record)
        outcome = CommandOutcome(success=resolved_success, result=dict(resolved_result), error=error)
        return CommandSubmission(
            command_id=f"stub-{len(calls)}", task_id=f"stub-task-{len(calls)}", outcome=outcome,
        )

    monkeypatch.setattr("stackowl.commands.spec.submit.submit_command", _fake)
    return calls
