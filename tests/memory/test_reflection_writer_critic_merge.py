"""FR-4 — reflection_writer composes CriticScorerHandler; one job does both.

Regression check for the merge: a fresh, unscored outcome must be scored AND
reflected in a SINGLE ``execute()`` call, since ``ReflectionStore.list_pending``
only picks up rows the critic has already scored (quality_score >= 0.6).
Before the merge this required two separate scheduler jobs on two separate
cadences (critic_scorer every 10m, reflection_writer every 15m) — a fresh
outcome could sit unreflected for up to ~25 minutes.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from stackowl.config.test_mode import TestModeGuard
from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.embeddings.registry import EmbeddingRegistry
from stackowl.memory.outcome_store import TaskOutcomeStore
from stackowl.memory.reflection_store import ReflectionStore
from stackowl.memory.reflection_writer_handler import ReflectionWriterHandler
from stackowl.providers.base import CompletionResult, Message
from stackowl.providers.registry import ProviderRegistry
from stackowl.scheduler.job import Job, JobResult

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _disable_test_mode_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """The handler calls assert_not_test_mode — neutralize as other handler tests do."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)


@dataclass
class _ScriptedProvider:
    """Stub provider returning canned strings in order from ``complete``."""

    responses: list[str]
    model_name: str = "stub-fast"
    _idx: int = 0

    @property
    def name(self) -> str:
        return "stub-fast"

    @property
    def protocol(self) -> Any:  # type: ignore[override]
        return "openai"

    async def complete(
        self, messages: list[Message], model: str = "", **kwargs: object
    ) -> CompletionResult:
        out = self.responses[self._idx]
        self._idx += 1
        return CompletionResult(
            content=out, model=self.model_name, provider_name="stub",
            input_tokens=0, output_tokens=0, duration_ms=1.0,
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3, 0.4] for _ in texts]


@pytest.fixture()
async def db(tmp_path: Path) -> AsyncIterator[DbPool]:
    db_path = tmp_path / "reflection_critic_merge.db"
    MigrationRunner(db_path=db_path).run()
    pool = DbPool(db_path=db_path)
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


def _job() -> Job:
    return Job(
        job_id="reflection_writer-test", handler_name="reflection_writer",
        schedule="every 15m", idempotency_key="reflection_writer",
        last_run_at=None, next_run_at="2026-07-01T00:00:00+00:00", status="running",
    )


async def test_execute_scores_then_reflects_a_fresh_outcome_in_one_run(
    db: DbPool,
) -> None:
    # A fresh outcome with NO quality_score yet. Before the merge, only the
    # SEPARATE critic_scorer job would ever see this row — reflection_writer
    # alone would skip it forever (list_pending requires quality_score >= 0.6).
    store = TaskOutcomeStore(db)
    await store.record(
        trace_id="merge-1", session_id="s", owl_name="secretary", channel="cli",
        success=True, latency_ms=10.0, tool_call_count=0,
        failure_class=None, step_durations={}, input_text="do a thing",
        response_text="solid answer",
    )
    out = await store.get_by_trace_id("merge-1")
    assert out is not None
    assert out.quality_score is None  # unscored — the whole point of this test

    provider = _ScriptedProvider(responses=[
        json.dumps({"score": 0.9}),  # critic phase call
        json.dumps({  # reflection phase call
            "summary": "answered cleanly on the first try",
            "suggested_strategy": "keep doing that",
        }),
    ])
    registry = ProviderRegistry()
    registry.register_mock("fast", provider, tier="fast")

    handler = ReflectionWriterHandler(
        db=db, provider_registry=registry, embedding_registry=EmbeddingRegistry(),
    )
    result = await handler.execute(_job())

    assert result.success is True
    assert result.output == "scored:1 written:1"
    assert result.metadata["scored"] == 1
    assert result.metadata["written"] == 1

    rescored = await store.get_by_trace_id("merge-1")
    assert rescored is not None
    assert rescored.quality_score == 0.9

    reflection = await ReflectionStore(db).get_by_trace_id("merge-1")
    assert reflection is not None
    assert reflection.summary == "answered cleanly on the first try"


async def test_critic_failure_does_not_block_reflection_of_already_scored_rows(
    db: DbPool,
) -> None:
    """Combine-error semantics: reflection still runs (and can succeed) even
    when the composed critic phase errors — favors resilience over an
    all-or-nothing job, per the merge design decision."""
    store = TaskOutcomeStore(db)
    await store.record(
        trace_id="merge-2", session_id="s", owl_name="secretary", channel="cli",
        success=True, latency_ms=10.0, tool_call_count=0,
        failure_class=None, step_durations={}, input_text="do a thing",
        response_text="solid answer",
    )
    out = await store.get_by_trace_id("merge-2")
    assert out is not None
    await store.set_quality_score(out.outcome_id, 0.8)  # already eligible

    provider = _ScriptedProvider(responses=[
        json.dumps({
            "summary": "worked first try",
            "suggested_strategy": "n/a",
        }),
    ])
    registry = ProviderRegistry()
    registry.register_mock("fast", provider, tier="fast")

    failing_critic = AsyncMock()
    failing_critic.execute.return_value = JobResult(
        job_id="reflection_writer-test", success=False, output=None,
        error="boom", duration_ms=1.0, metadata={"scored": 0},
    )

    handler = ReflectionWriterHandler(
        db=db, provider_registry=registry, embedding_registry=EmbeddingRegistry(),
        critic=failing_critic,
    )
    result = await handler.execute(_job())

    # Reflection pass still completed on the already-scored row.
    assert result.success is True
    assert result.metadata["written"] == 1
    # Critic failure is surfaced, not silently swallowed.
    assert result.error == "boom"
    failing_critic.execute.assert_awaited_once()


async def test_critic_phase_deferred_when_turn_registry_reports_active_load(
    db: DbPool,
) -> None:
    """CriticScorerHandler.defer_under_load=True must still be honored now
    that it's composed instead of independently scheduled — otherwise its
    heavy LLM batch would contend with live turns unconditionally, silently
    reversing a deliberate prior design decision (FR-4 gap fix)."""
    store = TaskOutcomeStore(db)
    await store.record(
        trace_id="merge-3", session_id="s", owl_name="secretary", channel="cli",
        success=True, latency_ms=10.0, tool_call_count=0,
        failure_class=None, step_durations={}, input_text="do a thing",
        response_text="solid answer",
    )

    registry = ProviderRegistry()
    busy_turn_registry = AsyncMock()
    busy_turn_registry.has_active_turns = lambda: True

    critic = AsyncMock()
    handler = ReflectionWriterHandler(
        db=db, provider_registry=registry, embedding_registry=EmbeddingRegistry(),
        critic=critic, turn_registry=busy_turn_registry,
    )
    result = await handler.execute(_job())

    # Critic phase was never invoked — deferred — so the row stays unscored
    # and (since it doesn't meet the reflection eligibility bar) unreflected.
    critic.execute.assert_not_awaited()
    assert result.metadata["scored"] == 0
    assert result.metadata["written"] == 0

    unscored = await store.get_by_trace_id("merge-3")
    assert unscored is not None
    assert unscored.quality_score is None


async def test_critic_phase_runs_when_no_active_turns(db: DbPool) -> None:
    """No load (or no turn_registry wired) — critic phase runs, matching the
    scheduler's own no-turn-registry-wired behavior (never skip)."""
    store = TaskOutcomeStore(db)
    await store.record(
        trace_id="merge-4", session_id="s", owl_name="secretary", channel="cli",
        success=True, latency_ms=10.0, tool_call_count=0,
        failure_class=None, step_durations={}, input_text="do a thing",
        response_text="solid answer",
    )

    provider = _ScriptedProvider(responses=[
        json.dumps({"score": 0.9}),
        json.dumps({"summary": "fine", "suggested_strategy": "n/a"}),
    ])
    registry = ProviderRegistry()
    registry.register_mock("fast", provider, tier="fast")

    idle_turn_registry = AsyncMock()
    idle_turn_registry.has_active_turns = lambda: False

    handler = ReflectionWriterHandler(
        db=db, provider_registry=registry, embedding_registry=EmbeddingRegistry(),
        turn_registry=idle_turn_registry,
    )
    result = await handler.execute(_job())

    assert result.metadata["scored"] == 1
    assert result.metadata["written"] == 1
