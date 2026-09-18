"""LAT.4 — reflection_writer_handler batches per-row writes into bounded
chunked transactions instead of one execute()-per-row autocommit.

Regression guard for pool.py's documented starvation failure mode: a chatty
background write loop holding/releasing the single SQLite writer once per
row. See story-LAT.4-batch-background-writes.md.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from stackowl.config.test_mode import TestModeGuard
from stackowl.db.pool import DbPool
from stackowl.embeddings.registry import EmbeddingRegistry
from stackowl.memory.outcome_store import TaskOutcomeStore
from stackowl.memory.reflection_store import ReflectionStore
from stackowl.memory.reflection_writer_handler import CHUNK_SIZE, ReflectionWriterHandler
from stackowl.providers.base import CompletionResult, Message
from stackowl.providers.registry import ModelRoute, ProviderRegistry
from stackowl.scheduler.job import Job
from tests._schema_template import seed_schema

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
    seed_schema(db_path)
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
            trace_id=trace_id, session_key="s", owl_name="secretary", channel="cli",
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


class TestMemoryReflectionRecordedJoinsTheSameCommit:
    """Story 2.8 -- each `reflections` row's chunk transaction ALSO writes a
    `memory.reflection_recorded` journal event, same trace_id, same commit
    (AD-24)."""

    async def test_each_row_gets_its_own_journal_event(self, db: DbPool) -> None:
        n = 3
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

        rows = await db.fetch_all(
            "SELECT trace_id, target_id, outcome, attention FROM journal_events "
            "WHERE type = 'memory.reflection_recorded' ORDER BY trace_id",
        )
        assert len(rows) == n
        assert {r["trace_id"] for r in rows} == {f"chunk-{i}" for i in range(n)}
        for row in rows:
            assert row["target_id"] == "secretary"
            assert row["outcome"] == "ok"
            assert row["attention"] == "ambient"

    async def test_a_journal_failure_mid_chunk_still_commits_the_reflection_row(
        self, db: DbPool, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A simulated `journal.record()` failure on ONE row must not roll
        back that row's `reflections` write, and must not stop later rows in
        the same chunk from committing either (B5)."""
        n = 3
        await _seed_pre_scored_outcomes(db, n)

        import stackowl.memory.reflection_writer_handler as handler_module

        real_record = handler_module.journal_record
        call_count = 0

        async def _flaky(conn: object, event: object) -> str:
            nonlocal call_count
            call_count += 1
            if call_count == 2:  # fail exactly the SECOND row's journal write
                raise RuntimeError("simulated journal.record failure")
            return await real_record(conn, event)  # type: ignore[arg-type]

        monkeypatch.setattr(handler_module, "journal_record", _flaky)

        registry = ProviderRegistry()
        registry.register_mock("fast", _ScriptedProvider(), tier="fast")
        critic = _NoOpCritic()

        handler = ReflectionWriterHandler(
            db=db, provider_registry=registry, embedding_registry=EmbeddingRegistry(),
            batch_limit=n, critic=critic,
        )
        result = await handler.execute(_job())

        # Every reflection row committed -- the chunk was NOT rolled back.
        assert result.metadata["written"] == n
        rstore = ReflectionStore(db)
        for i in range(n):
            ref = await rstore.get_by_trace_id(f"chunk-{i}")
            assert ref is not None
            assert ref.summary == "ok"

        # Only n - 1 journal rows exist -- the one simulated failure left no row.
        journal_rows = await db.fetch_all(
            "SELECT trace_id FROM journal_events WHERE type = 'memory.reflection_recorded'",
        )
        assert len(journal_rows) == n - 1

    async def test_a_real_journal_record_failure_degrades_health(
        self, db: DbPool,
    ) -> None:
        """AD-24's explicit clause: 'a failed record marks the journal health
        contributor degraded.' Unlike the mid-chunk test above (which
        monkeypatches the whole ``journal_record`` function, bypassing
        recorder.py's own degrade logic entirely), this forces a GENUINE
        internal ``journal.record()`` failure -- an unregistered event type,
        the same failure shape ``recorder.py``'s own
        ``get_registry().get(event.type)`` call raises on -- so recorder.py's
        real ``except`` block, real ``note_failure`` call, are what is under
        test here. The reflection row must still commit regardless (B5)."""
        from stackowl.journal.health import JournalHealthContributor
        from stackowl.journal.health import reset_for_tests as reset_journal_health
        from stackowl.journal.registry import get_registry

        n = 1
        await _seed_pre_scored_outcomes(db, n)

        registry = get_registry()
        saved_spec = registry._specs.pop("memory.reflection_recorded")  # noqa: SLF001 -- force recorder.py's REAL unregistered-type failure path
        try:
            registry_local = ProviderRegistry()
            registry_local.register_mock("fast", _ScriptedProvider(), tier="fast")
            handler = ReflectionWriterHandler(
                db=db, provider_registry=registry_local, embedding_registry=EmbeddingRegistry(),
                batch_limit=n, critic=_NoOpCritic(),
            )
            result = await handler.execute(_job())

            # The reflection row still committed -- only its journal row is missing.
            assert result.metadata["written"] == n
            rstore = ReflectionStore(db)
            ref = await rstore.get_by_trace_id("chunk-0")
            assert ref is not None and ref.summary == "ok"

            status = await JournalHealthContributor().health_check()
            assert status.status == "degraded"
        finally:
            registry._specs["memory.reflection_recorded"] = saved_spec  # noqa: SLF001
            # `journal/health.py`'s state is process-global and this directory
            # carries no autouse reset (unlike tests/journal/conftest.py) --
            # clear the streak THIS test deliberately caused so it cannot leak
            # a "degraded" status into an unrelated later test in the same run.
            reset_journal_health()

    async def test_canary_secrets_are_redacted_through_the_real_emitter(
        self, db: DbPool,
    ) -> None:
        """NFR33, the third of the three real recording paths this story
        wires (mirrors test_memory_events.py/test_consent_events.py's own
        proof for the other two) -- driven through the REAL chunk-loop
        emitter, not a synthetic JournalEvent."""
        canary = "Bearer sk-canary1234567890abcdefghijklmno"
        store = TaskOutcomeStore(db)
        await store.record(
            trace_id="chunk-canary", session_key="s", owl_name=canary, channel="cli",
            success=True, latency_ms=10.0, tool_call_count=0,
            failure_class=None, step_durations={}, input_text="do a thing",
            response_text="solid answer",
        )
        out = await store.get_by_trace_id("chunk-canary")
        assert out is not None
        await store.set_quality_score(out.outcome_id, 0.9)

        registry = ProviderRegistry()
        registry.register_mock("fast", _ScriptedProvider(), tier="fast")
        critic = _NoOpCritic()
        handler = ReflectionWriterHandler(
            db=db, provider_registry=registry, embedding_registry=EmbeddingRegistry(),
            batch_limit=1, critic=critic,
        )
        result = await handler.execute(_job())
        assert result.metadata["written"] == 1

        rows = await db.fetch_all(
            "SELECT attrs FROM journal_events WHERE type = 'memory.reflection_recorded' "
            "AND trace_id = ?",
            ("chunk-canary",),
        )
        assert len(rows) == 1
        stored = rows[0]["attrs"]
        assert canary not in stored
        assert json.loads(stored)["_redacted"] is True


@dataclass
class _ModelCapturingProvider:
    """Records the ``model`` kwarg passed to every ``complete()`` call —
    lets a test pin down that the SAME resolved model reaches EVERY row's
    ``_compute_reflection`` call in a multi-row batch, not just the first."""

    captured_models: list[str] = field(default_factory=list)
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
        self.captured_models.append(model)
        return CompletionResult(
            content=json.dumps({"summary": "ok", "suggested_strategy": "n/a"}),
            model=self.model_name, provider_name="stub",
            input_tokens=0, output_tokens=0, duration_ms=1.0,
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3, 0.4] for _ in texts]


_RESOLVED_MODEL = "reflection-tier-fast-v3"


async def test_resolved_model_reaches_every_row_in_a_multirow_batch(db: DbPool) -> None:
    """3 pre-scored rows, ONE provider resolution per execute() — the SAME
    resolved model string must reach ALL THREE rows' _compute_reflection ->
    provider.complete() calls, proving the batch loop threads it past row 0."""
    n = 3
    await _seed_pre_scored_outcomes(db, n)

    provider = _ModelCapturingProvider()
    registry = ProviderRegistry()
    registry.register_mock(
        "reflection-provider", provider,
        models=(ModelRoute(model=_RESOLVED_MODEL, tiers=("fast",)),),
    )
    critic = _NoOpCritic()

    handler = ReflectionWriterHandler(
        db=db, provider_registry=registry, embedding_registry=EmbeddingRegistry(),
        batch_limit=n, critic=critic,
    )
    result = await handler.execute(_job())

    assert result.success is True
    assert result.metadata["written"] == n

    # The load-bearing assertion: every row's complete() call carried the
    # SAME specific resolved model string — not "" and not just row 0.
    assert provider.captured_models == [_RESOLVED_MODEL] * n


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
