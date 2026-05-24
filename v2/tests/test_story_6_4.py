"""Story 6.4 — LanceDB adapter + SqliteMemoryBridge semantic recall tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from stackowl.db.pool import DbPool
from stackowl.memory.lancedb_adapter import LanceDBAdapter, SearchResult
from stackowl.memory.models import MemoryRecord
from stackowl.memory.sqlite_bridge import SqliteMemoryBridge

from tests._story_6_4_helpers import (  # noqa: F401 — fixtures re-exported
    FakeLanceDB,
    StubEmbeddingProvider,
    StubEmbeddingRegistry,
    db,
    insert_committed,
    no_test_mode_guard,
)


# ---------------------------------------------------------------------------
# LanceDBAdapter
# ---------------------------------------------------------------------------


def test_lancedb_search_result_frozen() -> None:
    sr = SearchResult(fact_id="x", score=0.5, metadata={})
    with pytest.raises(ValidationError):
        sr.fact_id = "y"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        SearchResult(fact_id="x", score=0.5, metadata={}, extra="no")  # type: ignore[call-arg]


async def test_lancedb_upsert_then_search(tmp_path: Path) -> None:
    adapter = LanceDBAdapter(data_dir=tmp_path / "lance")
    await adapter.upsert("f1", [0.1, 0.2, 0.3, 0.4], {"source": "conv"})
    await adapter.upsert("f2", [0.9, 0.8, 0.7, 0.6], {"source": "conv"})
    results = await adapter.search([0.1, 0.2, 0.3, 0.4], limit=2)
    assert len(results) >= 1
    assert any(r.fact_id == "f1" for r in results)


async def test_lancedb_search_no_table_returns_empty(tmp_path: Path) -> None:
    adapter = LanceDBAdapter(data_dir=tmp_path / "lance_empty")
    results = await adapter.search([0.1, 0.2, 0.3, 0.4], limit=5)
    assert results == []


async def test_lancedb_health_ok(tmp_path: Path) -> None:
    adapter = LanceDBAdapter(data_dir=tmp_path / "lance_h")
    report = await adapter.health()
    assert report.status in ("ok", "down")
    assert report.name == "memory.lancedb"


async def test_lancedb_reindex_batch(tmp_path: Path) -> None:
    adapter = LanceDBAdapter(data_dir=tmp_path / "lance_re")
    records = [
        (f"rid-{i}", [float(i), float(i + 1), float(i + 2), float(i + 3)], {"i": i})
        for i in range(5)
    ]
    count = await adapter.reindex(records)
    assert count == 5
    results = await adapter.search([0.0, 1.0, 2.0, 3.0], limit=5)
    assert len(results) >= 1


async def test_lancedb_delete(tmp_path: Path) -> None:
    adapter = LanceDBAdapter(data_dir=tmp_path / "lance_d")
    await adapter.upsert("d1", [0.1, 0.2, 0.3, 0.4], {"k": "v"})
    await adapter.delete("d1")
    results = await adapter.search([0.1, 0.2, 0.3, 0.4], limit=5)
    assert not any(r.fact_id == "d1" for r in results)


# ---------------------------------------------------------------------------
# SqliteMemoryBridge — semantic recall path
# ---------------------------------------------------------------------------


async def test_recall_uses_lancedb_when_semantic_enabled(db: DbPool) -> None:
    await insert_committed(db, "sem-1", "hello world")
    await insert_committed(db, "sem-2", "goodbye world")
    fake = FakeLanceDB(
        search_results=[
            SearchResult(fact_id="sem-2", score=0.95, metadata={}),
            SearchResult(fact_id="sem-1", score=0.40, metadata={}),
        ]
    )
    embed = StubEmbeddingRegistry(StubEmbeddingProvider(dim=4))
    bridge = SqliteMemoryBridge(
        db,
        embedding_registry=embed,  # type: ignore[arg-type]
        lancedb=fake,  # type: ignore[arg-type]
        semantic_search_enabled=True,
    )
    records = await bridge.recall("anything", limit=5)
    assert len(fake.searches) == 1
    assert [r.fact_id for r in records] == ["sem-2", "sem-1"]


async def test_recall_falls_back_to_fts5_when_lancedb_raises(db: DbPool) -> None:
    await insert_committed(db, "fb-1", "fallback content")
    fake = FakeLanceDB(raise_on_search=RuntimeError("lance boom"))
    embed = StubEmbeddingRegistry(StubEmbeddingProvider(dim=4))
    bridge = SqliteMemoryBridge(
        db,
        embedding_registry=embed,  # type: ignore[arg-type]
        lancedb=fake,  # type: ignore[arg-type]
        semantic_search_enabled=True,
    )
    records = await bridge.recall("fallback", limit=5)
    assert len(records) == 1
    assert records[0].fact_id == "fb-1"


async def test_recall_uses_fts_when_semantic_disabled(db: DbPool) -> None:
    await insert_committed(db, "f1", "kept fact")
    fake = FakeLanceDB(search_results=[])
    bridge = SqliteMemoryBridge(
        db,
        lancedb=fake,  # type: ignore[arg-type]
        semantic_search_enabled=False,
    )
    records = await bridge.recall("kept", limit=5)
    assert len(records) == 1
    assert fake.searches == []


async def test_recall_falls_back_when_no_embedder(db: DbPool) -> None:
    await insert_committed(db, "ne-1", "no embedder fact")
    fake = FakeLanceDB(
        search_results=[SearchResult(fact_id="ne-1", score=1.0, metadata={})]
    )
    bridge = SqliteMemoryBridge(
        db,
        embedding_registry=None,
        lancedb=fake,  # type: ignore[arg-type]
        semantic_search_enabled=True,
    )
    records = await bridge.recall("embedder", limit=5)
    assert len(records) == 1
    # No embedder means LanceDB cannot be queried — fell back to FTS5
    assert fake.searches == []


async def test_delete_calls_lancedb_when_present(db: DbPool) -> None:
    await insert_committed(db, "del-1", "to delete")
    fake = FakeLanceDB()
    bridge = SqliteMemoryBridge(db, lancedb=fake)  # type: ignore[arg-type]
    await bridge.delete("del-1")
    assert fake.deletes == ["del-1"]
    remaining = await db.fetch_all("SELECT fact_id FROM committed_facts")
    assert remaining == []


async def test_delete_swallows_lancedb_error(db: DbPool) -> None:
    await insert_committed(db, "del-x", "deletable")

    class _BoomLanceDB(FakeLanceDB):
        async def delete(self, fact_id: str) -> None:
            raise RuntimeError("lance delete failed")

    fake = _BoomLanceDB()
    bridge = SqliteMemoryBridge(db, lancedb=fake)  # type: ignore[arg-type]
    # Must not raise — SQLite delete must still proceed
    await bridge.delete("del-x")
    remaining = await db.fetch_all("SELECT fact_id FROM committed_facts")
    assert remaining == []


async def test_memory_record_returns_correct_shape(db: DbPool) -> None:
    await insert_committed(db, "shape-1", "shape-check content")
    fake = FakeLanceDB(
        search_results=[SearchResult(fact_id="shape-1", score=0.9, metadata={})]
    )
    embed = StubEmbeddingRegistry(StubEmbeddingProvider(dim=4))
    bridge = SqliteMemoryBridge(
        db,
        embedding_registry=embed,  # type: ignore[arg-type]
        lancedb=fake,  # type: ignore[arg-type]
        semantic_search_enabled=True,
    )
    records = await bridge.recall("anything", limit=1)
    assert len(records) == 1
    assert isinstance(records[0], MemoryRecord)
    assert records[0].fact_id == "shape-1"
