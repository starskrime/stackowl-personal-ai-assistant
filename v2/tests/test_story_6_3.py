"""Epic 6 Story 6.3 — Fact extractor & knowledge staging pipeline tests."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator, AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import pytest
from pydantic import ValidationError

from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.exceptions import FactExtractionParseError
from stackowl.memory.extraction_handler import FactExtractionJobHandler
from stackowl.memory.fact_extractor import (
    EXTRACTED_FACT_SOURCE_TYPE,
    ExtractedFactDraft,
    FactExtractor,
)
from stackowl.memory.fact_promoter import FactPromoter
from stackowl.memory.fact_reinforcer import FactReinforcer
from stackowl.memory.models import StagedFact
from stackowl.memory.pruner import MemoryPruner, PruneReport
from stackowl.memory.sqlite_bridge import SqliteMemoryBridge
from stackowl.memory.sqlite_helpers import pack_embedding
from stackowl.providers.base import CompletionResult, Message, ModelProvider
from stackowl.scheduler.job import Job

# ---------------------------------------------------------------------------
# Fixtures & doubles
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def no_test_mode_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "stackowl.config.test_mode.TestModeGuard.assert_not_test_mode",
        lambda *a, **kw: None,
    )


@pytest.fixture()
async def db(tmp_path: Path) -> AsyncGenerator[DbPool]:
    db_path = tmp_path / "story63.db"
    MigrationRunner(db_path=db_path).run()
    pool = DbPool(db_path=db_path)
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


class _MockProvider(ModelProvider):
    """In-memory provider that returns canned JSON for fact extraction."""

    def __init__(
        self,
        response: str = '[{"content": "The sky is blue", "confidence": 0.9}]',
    ) -> None:
        self._response = response
        self.call_count = 0
        self.last_messages: list[Message] | None = None

    @property
    def name(self) -> str:
        return "mock"

    @property
    def protocol(self) -> Literal["openai", "anthropic", "gemini"]:
        return "openai"

    async def complete(
        self, messages: list[Message], model: str, **kwargs: object
    ) -> CompletionResult:
        self.call_count += 1
        self.last_messages = messages
        return CompletionResult(
            content=self._response,
            input_tokens=10,
            output_tokens=5,
            model="mock",
            provider_name="mock",
            duration_ms=1.0,
        )

    def stream(
        self, messages: list[Message], model: str, **kwargs: object
    ) -> AsyncIterator[str]:
        raise NotImplementedError


class _StubEmbeddingProvider:
    """Simple deterministic embedder used by tests."""

    def __init__(
        self,
        dim: int = 4,
        name: str = "stub-embed",
        per_text: dict[str, list[float]] | None = None,
    ) -> None:
        self._dim = dim
        self._name = name
        self._per_text = per_text or {}

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for text in texts:
            if text in self._per_text:
                out.append(list(self._per_text[text]))
            else:
                seed = (sum(ord(c) for c in text) % 100) / 100.0 or 0.1
                out.append([seed * (i + 1) for i in range(self._dim)])
        return out

    @property
    def dimension(self) -> int:
        return self._dim

    @property
    def model_name(self) -> str:
        return self._name

    @property
    def is_local(self) -> bool:
        return True

    async def health_check(self) -> Any:  # pragma: no cover — unused
        return None


class _StubEmbeddingRegistry:
    """Registry stub that exposes ``.get()`` like the real one."""

    def __init__(self, provider: _StubEmbeddingProvider) -> None:
        self._provider = provider
        self._is_semantic = True

    def get(self) -> _StubEmbeddingProvider:
        return self._provider

    @property
    def is_semantic(self) -> bool:
        return self._is_semantic


async def _insert_committed(
    db: DbPool,
    fact_id: str,
    content: str,
    committed_at: str,
    embedding: bytes = b"\x00" * 4,
) -> None:
    await db.execute(
        """INSERT INTO committed_facts
               (fact_id, content, embedding, embedding_model, committed_at,
                source_type, source_ref, tags)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            fact_id,
            content,
            embedding,
            "test",
            committed_at,
            "conversation",
            "sess-x",
            "[]",
        ),
    )
    rows = await db.fetch_all(
        "SELECT rowid AS rid FROM committed_facts WHERE fact_id = ?", (fact_id,)
    )
    await db.execute(
        "INSERT INTO committed_facts_fts(rowid, content) VALUES (?, ?)",
        (rows[0]["rid"], content),
    )


async def _insert_staged(
    db: DbPool,
    *,
    fact_id: str,
    content: str = "a fact",
    confidence: float = 0.9,
    reinforcement_count: int = 0,
    status: str = "staged",
    embedding: list[float] | None = None,
) -> None:
    await db.execute(
        """INSERT INTO staged_facts (
               fact_id, content, source_type, source_ref, confidence,
               staged_at, reinforcement_count, status, embedding, embedding_model
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            fact_id,
            content,
            "conversation",
            "sess-x",
            confidence,
            datetime.now(UTC).isoformat(),
            reinforcement_count,
            status,
            pack_embedding(embedding),
            "test" if embedding else None,
        ),
    )


# ---------------------------------------------------------------------------
# ExtractedFactDraft model
# ---------------------------------------------------------------------------


def test_extracted_fact_draft_model_frozen() -> None:
    draft = ExtractedFactDraft(content="hi", confidence=0.5)
    with pytest.raises(ValidationError):
        draft.content = "no"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        ExtractedFactDraft(content="x", confidence=0.5, extra="forbidden")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# FactExtractor
# ---------------------------------------------------------------------------


async def test_fact_extractor_returns_staged_facts() -> None:
    provider = _MockProvider()
    extractor = FactExtractor(provider=provider, sensitive_categories=[])
    facts = await extractor.extract(
        [Message(role="user", content="Hello")], session_id="s1"
    )
    assert len(facts) == 1
    assert isinstance(facts[0], StagedFact)
    assert facts[0].content == "The sky is blue"
    assert abs(facts[0].confidence - 0.9) < 1e-6


async def test_fact_extractor_sets_source_type_conversation() -> None:
    provider = _MockProvider()
    extractor = FactExtractor(provider=provider)
    facts = await extractor.extract(
        [Message(role="user", content="anything")], session_id="sess-A"
    )
    # Extracted facts are tagged conversation_fact (commit 6c6ec0c) — distinct from raw
    # 'conversation' turns so they don't pollute Plan A short-term history. Assert against
    # the canonical constant so this can't go stale again.
    assert facts[0].source_type == EXTRACTED_FACT_SOURCE_TYPE  # "conversation_fact"
    assert facts[0].source_ref == "sess-A"


async def test_fact_extractor_strips_markdown_fences() -> None:
    provider = _MockProvider(
        response='```json\n[{"content": "x", "confidence": 0.5}]\n```'
    )
    extractor = FactExtractor(provider=provider)
    facts = await extractor.extract([Message(role="user", content="hi")], "s")
    assert len(facts) == 1
    assert facts[0].content == "x"


async def test_fact_extractor_parse_error_raises() -> None:
    provider = _MockProvider(response="not json at all {{{")
    extractor = FactExtractor(provider=provider)
    with pytest.raises(FactExtractionParseError) as exc_info:
        await extractor.extract([Message(role="user", content="hi")], "s")
    assert exc_info.value.raw_response_excerpt.startswith("not json")


async def test_fact_extractor_sensitive_category_filtered() -> None:
    provider = _MockProvider(
        response=json.dumps(
            [
                {"content": "user wrote password=hunter2", "confidence": 0.9},
                {"content": "The sky is blue", "confidence": 0.9},
            ]
        )
    )
    extractor = FactExtractor(
        provider=provider, sensitive_categories=["password"]
    )
    facts = await extractor.extract([Message(role="user", content="x")], "s")
    assert len(facts) == 1
    assert "password" not in facts[0].content


async def test_fact_extractor_sensitive_not_logged_raw(
    capture_logs: list[dict[str, Any]],
) -> None:
    secret_payload = "hunter2-very-secret-string"
    provider = _MockProvider(
        response=json.dumps(
            [{"content": f"password={secret_payload}", "confidence": 0.9}]
        )
    )
    extractor = FactExtractor(
        provider=provider, sensitive_categories=["password"]
    )
    facts = await extractor.extract([Message(role="user", content="x")], "s")
    assert facts == []
    # The raw secret must NEVER appear anywhere in captured logs.
    serialized = json.dumps(capture_logs)
    assert secret_payload not in serialized


async def test_fact_extractor_embeds_facts() -> None:
    provider = _MockProvider()
    embed_provider = _StubEmbeddingProvider(dim=4)
    registry = _StubEmbeddingRegistry(embed_provider)
    extractor = FactExtractor(provider=provider, embedding_registry=registry)  # type: ignore[arg-type]
    facts = await extractor.extract([Message(role="user", content="hi")], "s")
    assert facts[0].embedding is not None
    assert len(facts[0].embedding) == 4
    assert facts[0].embedding_model == "stub-embed"


async def test_fact_extractor_no_embedder_logs_warning(
    capture_logs: list[dict[str, Any]],
) -> None:
    provider = _MockProvider()
    extractor = FactExtractor(provider=provider, embedding_registry=None)
    facts = await extractor.extract([Message(role="user", content="hi")], "s")
    assert facts[0].embedding is None
    assert facts[0].embedding_model is None
    warning_msgs = [
        r for r in capture_logs if r["level"] == "WARNING" and "embedding" in r["msg"]
    ]
    assert warning_msgs, "expected a WARNING about missing embedder"


# ---------------------------------------------------------------------------
# FactPromoter
# ---------------------------------------------------------------------------


async def test_fact_promoter_promote_eligible_dual_gate(db: DbPool) -> None:
    fact_id = str(uuid.uuid4())
    await _insert_staged(
        db,
        fact_id=fact_id,
        content="committed fact",
        confidence=0.9,
        reinforcement_count=3,
        embedding=[0.1, 0.2, 0.3],
    )
    promoter = FactPromoter(db, confidence_threshold=0.8, reinforcement_required=3)
    promoted = await promoter.promote_eligible()
    assert promoted == 1
    rows = await db.fetch_all("SELECT fact_id FROM committed_facts")
    assert any(r["fact_id"] == fact_id for r in rows)
    staged_status = await db.fetch_all(
        "SELECT status FROM staged_facts WHERE fact_id = ?", (fact_id,)
    )
    assert staged_status[0]["status"] == "committed"


async def test_fact_promoter_not_promoted_low_confidence(db: DbPool) -> None:
    await _insert_staged(
        db,
        fact_id="low-conf",
        confidence=0.5,
        reinforcement_count=3,
    )
    promoter = FactPromoter(db, confidence_threshold=0.8, reinforcement_required=3)
    promoted = await promoter.promote_eligible()
    assert promoted == 0
    rows = await db.fetch_all("SELECT * FROM committed_facts")
    assert rows == []


async def test_fact_promoter_not_promoted_low_reinforcement(db: DbPool) -> None:
    await _insert_staged(
        db,
        fact_id="low-reinf",
        confidence=0.9,
        reinforcement_count=1,
    )
    promoter = FactPromoter(db, confidence_threshold=0.8, reinforcement_required=3)
    promoted = await promoter.promote_eligible()
    assert promoted == 0


async def test_fact_promoter_force_promote_bypasses_gates(db: DbPool) -> None:
    await _insert_staged(
        db,
        fact_id="forced",
        content="weak fact",
        confidence=0.1,
        reinforcement_count=0,
    )
    promoter = FactPromoter(db, confidence_threshold=0.8, reinforcement_required=3)
    ok = await promoter.force_promote("forced")
    assert ok is True
    rows = await db.fetch_all("SELECT fact_id FROM committed_facts")
    assert any(r["fact_id"] == "forced" for r in rows)


async def test_fact_promoter_force_promote_unknown_id(db: DbPool) -> None:
    promoter = FactPromoter(db)
    assert await promoter.force_promote("does-not-exist") is False


# ---------------------------------------------------------------------------
# FactReinforcer
# ---------------------------------------------------------------------------


async def test_fact_reinforcer_increments_count(db: DbPool) -> None:
    same_vec = [1.0, 0.0, 0.0, 0.0]
    await _insert_staged(
        db,
        fact_id="reinf-1",
        content="boss loves verification",
        embedding=same_vec,
    )
    embed_provider = _StubEmbeddingProvider(
        dim=4, per_text={"verification matters": same_vec}
    )
    registry = _StubEmbeddingRegistry(embed_provider)
    reinforcer = FactReinforcer(
        db, embedding_registry=registry, similarity_threshold=0.5  # type: ignore[arg-type]
    )
    count = await reinforcer.reinforce_from_conversation(
        "conv-1", "verification matters"
    )
    assert count == 1
    rows = await db.fetch_all(
        "SELECT reinforcement_count FROM staged_facts WHERE fact_id = 'reinf-1'"
    )
    assert rows[0]["reinforcement_count"] == 1


async def test_fact_reinforcer_no_increment_dissimilar(db: DbPool) -> None:
    await _insert_staged(
        db,
        fact_id="reinf-2",
        embedding=[1.0, 0.0, 0.0, 0.0],
    )
    embed_provider = _StubEmbeddingProvider(
        dim=4, per_text={"unrelated": [0.0, 1.0, 0.0, 0.0]}
    )
    registry = _StubEmbeddingRegistry(embed_provider)
    reinforcer = FactReinforcer(
        db, embedding_registry=registry, similarity_threshold=0.5  # type: ignore[arg-type]
    )
    count = await reinforcer.reinforce_from_conversation("conv-x", "unrelated")
    assert count == 0
    rows = await db.fetch_all(
        "SELECT reinforcement_count FROM staged_facts WHERE fact_id = 'reinf-2'"
    )
    assert rows[0]["reinforcement_count"] == 0


async def test_fact_reinforcer_no_embedder_returns_zero(
    db: DbPool, capture_logs: list[dict[str, Any]]
) -> None:
    reinforcer = FactReinforcer(db, embedding_registry=None)
    count = await reinforcer.reinforce_from_conversation("c", "summary")
    assert count == 0
    warnings = [
        r for r in capture_logs if r["level"] == "WARNING" and "embedding" in r["msg"]
    ]
    assert warnings


# ---------------------------------------------------------------------------
# MemoryPruner
# ---------------------------------------------------------------------------


def test_prune_report_model_frozen() -> None:
    report = PruneReport(pruned_count=1, kept_count=2)
    with pytest.raises(ValidationError):
        report.pruned_count = 9  # type: ignore[misc]
    with pytest.raises(ValidationError):
        PruneReport(pruned_count=1, kept_count=2, extra="nope")  # type: ignore[call-arg]


async def test_memory_pruner_prunes_stale(db: DbPool) -> None:
    fact_id = "stale-1"
    await _insert_staged(
        db,
        fact_id=fact_id,
        confidence=0.1,
        reinforcement_count=0,
        status="committed",
    )
    # committed 200 days ago
    old_iso = "2024-01-01T00:00:00+00:00"
    await _insert_committed(db, fact_id, "stale content", old_iso)

    pruner = MemoryPruner(db, prune_after_days=90, confidence_threshold=0.4)
    report = await pruner.prune()
    assert report.pruned_count == 1
    remaining = await db.fetch_all("SELECT * FROM committed_facts")
    assert remaining == []
    staged_remaining = await db.fetch_all(
        "SELECT * FROM staged_facts WHERE fact_id = ?", (fact_id,)
    )
    assert staged_remaining == []


async def test_memory_pruner_keeps_reinforced(db: DbPool) -> None:
    fact_id = "kept-1"
    await _insert_staged(
        db,
        fact_id=fact_id,
        confidence=0.1,
        reinforcement_count=2,  # reinforced — must be kept
        status="committed",
    )
    await _insert_committed(db, fact_id, "kept", "2024-01-01T00:00:00+00:00")
    pruner = MemoryPruner(db, prune_after_days=90, confidence_threshold=0.4)
    report = await pruner.prune()
    assert report.pruned_count == 0
    assert report.kept_count == 1
    rows = await db.fetch_all("SELECT fact_id FROM committed_facts")
    assert any(r["fact_id"] == fact_id for r in rows)


# ---------------------------------------------------------------------------
# FactExtractionJobHandler
# ---------------------------------------------------------------------------


async def test_extraction_handler_extracts_and_stages(db: DbPool) -> None:
    # Seed a conversation row + a couple of messages.
    conv_id = "conv-extract-1"
    session_id = "sess-extract-1"
    now = datetime.now(UTC).isoformat()
    await db.execute(
        """INSERT INTO conversations (id, session_id, owl_name, started_at, message_count)
           VALUES (?, ?, ?, ?, ?)""",
        (conv_id, session_id, "secretary", now, 1),
    )
    await db.execute(
        """INSERT INTO messages (id, conversation_id, role, content, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        ("m1", conv_id, "user", "I prefer detailed responses", now),
    )

    provider = _MockProvider(
        response='[{"content": "user prefers detail", "confidence": 0.85}]'
    )
    extractor = FactExtractor(provider=provider, sensitive_categories=[])
    bridge = SqliteMemoryBridge(db)
    handler = FactExtractionJobHandler(extractor, bridge, db)

    job = Job(
        job_id="j1",
        handler_name="fact_extraction",
        schedule="manual",
        idempotency_key=f"fact_extraction:{session_id}",
        last_run_at=None,
        next_run_at=now,
        status="pending",
        retry_count=0,
    )
    result = await handler.execute(job)
    assert result.success is True
    staged = await bridge.list_staged()
    assert len(staged) == 1
    assert staged[0].content == "user prefers detail"


async def test_extraction_handler_invalid_idempotency_key(db: DbPool) -> None:
    provider = _MockProvider()
    extractor = FactExtractor(provider=provider)
    bridge = SqliteMemoryBridge(db)
    handler = FactExtractionJobHandler(extractor, bridge, db)
    job = Job(
        job_id="j2",
        handler_name="fact_extraction",
        schedule="manual",
        idempotency_key="not-prefixed",
        last_run_at=None,
        next_run_at=datetime.now(UTC).isoformat(),
        status="pending",
        retry_count=0,
    )
    result = await handler.execute(job)
    assert result.success is False
    assert result.error is not None
