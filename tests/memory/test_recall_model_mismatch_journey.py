"""F062 (P0) merge-gate journey — recall must NOT serve a mismatched-corpus
vector as a "confirmed" fact after the embedding model changes.

Scenario: a committed fact was embedded + indexed under ``hash-v1-384d`` (the
degraded fallback) and the sidecar records that identity. The platform then
boots with a SEMANTIC registry (``all-MiniLM-L6-v2``) — same 384 dims, DIFFERENT
model. The ANN corpus is now poisoned relative to the active model.

Assert the corpus-LEVEL gate in ``SqliteMemoryBridge.recall``:
  (a) the hash fact is NOT returned through the semantic/ANN path,
  (b) it IS still surfaced through FTS (recall degrades honestly, never empty),
  (c) a WARNING naming BOTH models is logged,
  (d) ``LanceDBAdapter.health()`` reports ``degraded`` with both model fields.
And: a freshly-written SAME-model fact still returns via ANN (no over-block).

Mocks ONLY the embedding provider (deterministic vectors + model_name); real
LanceDB / SQLite / FTS5 throughout.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from stackowl.config.test_mode import TestModeGuard
from stackowl.db.pool import DbPool
from stackowl.embeddings.base import EmbeddingProvider
from stackowl.embeddings.registry import EmbeddingRegistry
from stackowl.health.status import HealthStatus
from stackowl.memory.fact_promoter import FactPromoter
from stackowl.memory.lancedb_adapter import LanceDBAdapter
from stackowl.memory.lancedb_helpers import read_corpus_identity, write_corpus_identity
from stackowl.memory.sqlite_bridge import SqliteMemoryBridge

pytestmark = pytest.mark.asyncio

_FACT_CONTENT = "The Otto Ninja starter robot kit ships with two servos"


class _FakeProvider(EmbeddingProvider):
    """A deterministic in-memory provider with a configurable model_name."""

    def __init__(self, model_name: str, dim: int = 384) -> None:
        self._model = model_name
        self._dim = dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        # Deterministic non-zero vector; content-independent is fine — the gate
        # under test is the corpus-model check, not vector quality.
        return [[0.1] * self._dim for _ in texts]

    @property
    def dimension(self) -> int:
        return self._dim

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def is_local(self) -> bool:
        return True

    async def health_check(self) -> HealthStatus:
        return HealthStatus(name=f"embedding_{self._model}", status="ok", message=None, latency_ms=0.0)


def _registry_with(provider: EmbeddingProvider) -> EmbeddingRegistry:
    registry = EmbeddingRegistry()
    registry._provider = provider  # type: ignore[attr-defined]
    registry._is_semantic = True  # type: ignore[attr-defined]
    return registry


async def _seed_committed_fact(db: DbPool, *, fact_id: str, content: str, model: str) -> None:
    await db.execute(
        """INSERT INTO staged_facts (
               fact_id, content, source_type, source_ref, confidence,
               staged_at, reinforcement_count, status, embedding, embedding_model
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            fact_id, content, "conversation_fact", "sess-mismatch", 0.9,
            datetime.now(UTC).isoformat(), 1, "staged", b"", model,
        ),
    )
    promoter = FactPromoter(
        db,
        confidence_threshold=0.8,
        reinforcement_required=3,
        conversation_fact_reinforcement_required=1,
    )
    promoted = await promoter.promote_eligible()
    assert promoted == 1


async def test_model_mismatch_recall_no_poison_fts_honest(
    tmp_db: DbPool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capture_logs: list,
) -> None:
    monkeypatch.setattr(TestModeGuard, "assert_not_test_mode", staticmethod(lambda _op: None))

    fact_id = str(uuid.uuid4())
    await _seed_committed_fact(tmp_db, fact_id=fact_id, content=_FACT_CONTENT, model="hash-v1-384d")

    # Boot with a SEMANTIC registry: same 384-d, different model.
    embeddings = _registry_with(_FakeProvider("all-MiniLM-L6-v2"))
    lancedb = LanceDBAdapter(data_dir=tmp_path / "lance", embedding_registry=embeddings)
    # Index the vector under the HASH corpus + tag the sidecar as hash.
    await lancedb.upsert(fact_id, [0.2] * 384, {"content": _FACT_CONTENT, "embedding_model": "hash-v1-384d"})
    conn = lancedb._connect()  # type: ignore[attr-defined]
    write_corpus_identity(conn, "hash-v1-384d", 384)
    assert read_corpus_identity(conn) == ("hash-v1-384d", 384)

    bridge = SqliteMemoryBridge(
        tmp_db, embedding_registry=embeddings, lancedb=lancedb, semantic_search_enabled=True
    )

    results = await bridge.recall("ninja robot", limit=5)

    # (a)+(b) the fact is recalled (via FTS, the honest degrade) — never silently dropped.
    assert any(r.fact_id == fact_id for r in results), (
        "mismatched-corpus fact must STILL surface via FTS, never vanish"
    )

    # (c) a WARNING named BOTH models.
    blob = " ".join(str(rec) for rec in capture_logs)
    assert "all-MiniLM-L6-v2" in blob and "hash-v1-384d" in blob, (
        f"mismatch WARNING must name active + corpus model; logs={blob[:600]}"
    )

    # (d) health degraded with both model fields.
    health = await lancedb.health()
    assert health.status == "degraded"
    assert health.details.get("corpus_embedding_model") == "hash-v1-384d"
    assert health.details.get("active_embedding_model") == "all-MiniLM-L6-v2"


async def test_same_model_recall_uses_ann(
    tmp_db: DbPool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fact written under the SAME active model is still returned via ANN."""
    monkeypatch.setattr(TestModeGuard, "assert_not_test_mode", staticmethod(lambda _op: None))

    fact_id = str(uuid.uuid4())
    await _seed_committed_fact(
        tmp_db, fact_id=fact_id, content=_FACT_CONTENT, model="all-MiniLM-L6-v2"
    )

    embeddings = _registry_with(_FakeProvider("all-MiniLM-L6-v2"))
    lancedb = LanceDBAdapter(data_dir=tmp_path / "lance", embedding_registry=embeddings)
    await lancedb.upsert(fact_id, [0.2] * 384, {"content": _FACT_CONTENT, "embedding_model": "all-MiniLM-L6-v2"})
    conn = lancedb._connect()  # type: ignore[attr-defined]
    write_corpus_identity(conn, "all-MiniLM-L6-v2", 384)

    bridge = SqliteMemoryBridge(
        tmp_db, embedding_registry=embeddings, lancedb=lancedb, semantic_search_enabled=True
    )

    from stackowl.memory.sqlite_helpers import semantic_recall

    semantic = await semantic_recall(tmp_db, embeddings, lancedb, "ninja robot", 5)
    assert semantic, "same-model corpus must serve the ANN path"
    assert any(r.fact_id == fact_id for r in semantic)

    results = await bridge.recall("ninja robot", limit=5)
    assert any(r.fact_id == fact_id for r in results)

    health = await lancedb.health()
    assert health.status == "ok"
