"""Regression: recall() must fall back to FTS5 when semantic search yields no hits.

Bug: ``SqliteMemoryBridge.recall()`` guarded the semantic result with
``if semantic is not None:`` — but ``semantic_recall`` returns an empty list
``[]`` (not ``None``) when the LanceDB ``committed_facts`` table does not exist
(``sync_search`` short-circuits to ``[]``). Since ``[] is not None`` is True,
recall() returned the empty list and NEVER reached the FTS5 fallback — so a
fact that IS committed and IS matchable by FTS5 was never surfaced.

This test wires the bridge WITH a LanceDB adapter pointed at an EMPTY temp dir
(table missing → semantic path returns ``[]``) and asserts the committed fact
is still recalled via FTS5.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from stackowl.config.test_mode import TestModeGuard
from stackowl.db.pool import DbPool
from stackowl.embeddings.registry import EmbeddingRegistry
from stackowl.memory.fact_promoter import FactPromoter
from stackowl.memory.lancedb_adapter import LanceDBAdapter
from stackowl.memory.sqlite_bridge import SqliteMemoryBridge

pytestmark = pytest.mark.asyncio

# Distinctive token unlikely to collide with any other seeded fact.
_FACT_CONTENT = "The Otto Ninja starter robot kit ships with two servos"


async def _seed_committed_fact(db: DbPool, *, fact_id: str, content: str) -> None:
    """Seed one committed fact via the production promotion path.

    Inserts a staged conversation_fact eligible for promotion, then runs the
    real :class:`FactPromoter` so both ``committed_facts`` AND
    ``committed_facts_fts`` get written exactly as production does.
    """
    from datetime import UTC, datetime

    await db.execute(
        """INSERT INTO staged_facts (
               fact_id, content, source_type, source_ref, confidence,
               staged_at, reinforcement_count, status, embedding, embedding_model
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            fact_id,
            content,
            "conversation_fact",
            "sess-fallback",
            0.9,
            datetime.now(UTC).isoformat(),
            1,
            "staged",
            b"",
            None,
        ),
    )
    promoter = FactPromoter(
        db,
        confidence_threshold=0.8,
        reinforcement_required=3,
        conversation_fact_reinforcement_required=1,
    )
    promoted = await promoter.promote_eligible()
    assert promoted == 1, "fixture precondition: fact must promote into committed_facts"


async def test_recall_falls_back_to_fts5_when_semantic_empty(
    tmp_db: DbPool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Committed fact is surfaced via FTS5 even when LanceDB returns no hits."""
    # Allow live LanceDB I/O for this test (adapter gates on TestModeGuard).
    monkeypatch.setattr(TestModeGuard, "assert_not_test_mode", staticmethod(lambda _op: None))

    fact_id = str(uuid.uuid4())
    await _seed_committed_fact(tmp_db, fact_id=fact_id, content=_FACT_CONTENT)

    # LanceDB pointed at an EMPTY dir → committed_facts table does NOT exist →
    # search() returns [] → semantic_recall() returns [] (the bug trigger).
    lancedb = LanceDBAdapter(data_dir=tmp_path / "empty_lancedb")
    # Bare registry: .get() lazily yields the hash provider — deterministic,
    # no model download, sufficient to drive the semantic path to its [] result.
    embeddings = EmbeddingRegistry()

    bridge = SqliteMemoryBridge(
        tmp_db,
        embedding_registry=embeddings,
        lancedb=lancedb,
        semantic_search_enabled=True,
    )

    # Sanity: the semantic path genuinely yields [] (table missing).
    from stackowl.memory.sqlite_helpers import semantic_recall

    semantic = await semantic_recall(tmp_db, embeddings, lancedb, "ninja robot", 5)
    assert semantic == [], (
        "precondition: semantic path must return [] (empty LanceDB table), "
        f"got {semantic!r}"
    )

    results = await bridge.recall("ninja robot", limit=5)

    assert results, "recall() must fall back to FTS5 and return the committed fact"
    assert any(r.fact_id == fact_id for r in results), (
        f"recall() must surface the seeded fact via FTS5; "
        f"got {[r.fact_id for r in results]}"
    )
    assert any("Ninja" in r.content for r in results), (
        "recalled record content must match the seeded fact"
    )
