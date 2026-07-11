"""LAT.4 — reflection_writer_handler batches per-row writes into bounded
chunked transactions instead of one execute()-per-row autocommit.

Regression guard for pool.py's documented starvation failure mode: a chatty
background write loop holding/releasing the single SQLite writer once per
row. See story-LAT.4-batch-background-writes.md.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from stackowl.config.test_mode import TestModeGuard
from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.embeddings.registry import EmbeddingRegistry
from stackowl.memory.outcome_store import TaskOutcomeStore
from stackowl.memory.reflection_store import ReflectionStore
from stackowl.memory.reflection_writer_handler import CHUNK_SIZE, ReflectionWriterHandler
from stackowl.providers.base import CompletionResult, Message
from stackowl.providers.registry import ProviderRegistry
from stackowl.scheduler.job import Job

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _disable_test_mode_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)


@dataclass
class _ScriptedProvider:
    """Always returns a valid reflection JSON payload (critic phase not used
    here — outcomes are pre-scored so only the reflection phase runs)."""

    model_name: str = "stub-fast"

    @property
    def name(self) -> str:
        return "stub-fast"

    @property
    def protocol(self) -> Any:  # type: ignore[override]
        return "openai"

    async def complete(
        self, messages: list[Message], model: str = "", **kwargs: object
    ) -> CompletionResult:
        return CompletionResult(
            content=json.dumps({"summary": "ok", "suggested_strategy": "n/a"}),
            model=self.model_name, provider_name="stub",
            input_tokens=0, output_tokens=0, duration_ms=1.0,
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3, 0.4] for _ in texts]


@pytest.fixture()
async def db(tmp_path: Path) -> AsyncIterator[DbPool]:
    db_path = tmp_path / "reflection_chunking.db"
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


async def _seed_pre_scored_outcomes(db: DbPool, n: int) -> None:
    store = TaskOutcomeStore(db)
    for i in range(n):
        trace_id = f"chunk-{i}"
        await store.record(
            trace_id=trace_id, session_id="s", owl_name="secretary", channel="cli",
            success=True, latency_ms=10.0, tool_call_count=0,
            failure_class=None, step_durations={}, input_text="do a thing",
            response_text="solid answer",
        )
        out = await store.get_by_trace_id(trace_id)
        assert out is not None
        await store.set_quality_score(out.outcome_id, 0.9)  # eligible without critic phase


async def test_batch_larger_than_chunk_size_commits_in_multiple_bounded_chunks(
    db: DbPool,
) -> None:
    """N > CHUNK_SIZE rows must commit in ceil(N/CHUNK_SIZE) transactions, not
    one transaction for the whole batch and not one per row."""
    n = CHUNK_SIZE * 2 + 5
    await _seed_pre_scored_outcomes(db, n)

    tx_calls = 0
    orig_transaction = db.transaction

    def counting_transaction():  # type: ignore[no-untyped-def]
        nonlocal tx_calls
        tx_calls += 1
        return orig_transaction()

    db.transaction = counting_transaction  # type: ignore[method-assign]

    registry = ProviderRegistry()
    registry.register_mock("fast", _ScriptedProvider(), tier="fast")
    critic = _NoOpCritic()

    handler = ReflectionWriterHandler(
        db=db, provider_registry=registry, embedding_registry=EmbeddingRegistry(),
        batch_limit=n, critic=critic,
    )
    result = await handler.execute(_job())

    assert result.success is True
    assert result.metadata["written"] == n
    # ceil(n / CHUNK_SIZE) == 3 for n = 2*CHUNK_SIZE + 5
    assert tx_calls == 3
    # No single chunk exceeds the bound (AC #3).
    assert CHUNK_SIZE <= 100


async def test_chunk_size_constant_is_within_story_bound() -> None:
    assert 50 <= CHUNK_SIZE <= 100


async def test_all_rows_persisted_across_chunk_boundary(db: DbPool) -> None:
    n = CHUNK_SIZE + 1  # forces exactly 2 chunks
    await _seed_pre_scored_outcomes(db, n)

    registry = ProviderRegistry()
    registry.register_mock("fast", _ScriptedProvider(), tier="fast")
    critic = _NoOpCritic()

    handler = ReflectionWriterHandler(
        db=db, provider_registry=registry, embedding_registry=EmbeddingRegistry(),
        batch_limit=n, critic=critic,
    )
    result = await handler.execute(_job())
    assert result.metadata["written"] == n

    rstore = ReflectionStore(db)
    for i in range(n):
        ref = await rstore.get_by_trace_id(f"chunk-{i}")
        assert ref is not None
        assert ref.summary == "ok"


class _NoOpCritic:
    """Critic stub — outcomes are pre-scored so the critic phase is skipped
    entirely (avoids needing a second scripted provider call per row)."""

    defer_under_load = False

    async def execute(self, job: Job) -> Any:
        from stackowl.scheduler.job import JobResult

        return JobResult(
            job_id=job.job_id, effect_class="state_change", success=True,
            output="noop", error=None, duration_ms=0.1,
            metadata={"scored": 0, "pending_count": 0},
        )
