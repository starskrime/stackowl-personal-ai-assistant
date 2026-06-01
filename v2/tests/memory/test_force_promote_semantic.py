"""Bug B2 — explicitly-remembered facts must be EMBEDDED and SEMANTICALLY recallable.

Root cause (pre-fix):
- ``remember_fact`` built the :class:`StagedFact` with NO embedding, so the
  committed_facts row landed with a zero-length embedding BLOB and empty
  embedding_model.
- ``FactPromoter._promote_one`` never upserted a vector to LanceDB (and was never
  even handed a LanceDB adapter), so the ``committed_facts`` ANN table was never
  created and semantic recall could never surface a remembered fact.

These tests drive the production ``remember_fact`` chokepoint with a real
embedding registry (lazy hash fallback — deterministic, no model download) and a
``FactPromoter`` wired to a temp-dir ``LanceDBAdapter``. They assert:

1. The committed_facts row has a NON-EMPTY embedding BLOB and a non-empty
   embedding_model (RED pre-fix: empty BLOB / empty model).
2. The remembered fact is recallable via the SEMANTIC path (LanceDB), i.e. it now
   has a vector in the ANN table (RED pre-fix: no vector, no table).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stackowl.commands.memory_helpers import remember_fact
from stackowl.config.test_mode import TestModeGuard
from stackowl.db.pool import DbPool
from stackowl.embeddings.registry import EmbeddingRegistry
from stackowl.memory.fact_promoter import FactPromoter
from stackowl.memory.lancedb_adapter import LanceDBAdapter
from stackowl.memory.sqlite_bridge import SqliteMemoryBridge
from stackowl.memory.sqlite_helpers import semantic_recall

pytestmark = pytest.mark.asyncio

# Distinctive content unlikely to collide with any other seeded fact. Because the
# hash provider is deterministic, embedding this exact text as the query yields
# the identical vector — guaranteeing it is its own nearest neighbour in LanceDB.
_FACT_CONTENT = "The user's production database lives in the eu-west-1 region"


async def test_remember_fact_embeds_committed_fact(
    tmp_db: DbPool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A remembered fact's committed_facts row must carry a non-empty embedding."""
    # Allow live LanceDB I/O (adapter + promoter gate on TestModeGuard).
    monkeypatch.setattr(
        TestModeGuard, "assert_not_test_mode", staticmethod(lambda _op: None)
    )

    embeddings = EmbeddingRegistry()  # lazy hash fallback — deterministic
    lancedb = LanceDBAdapter(data_dir=tmp_path / "lancedb")
    bridge = SqliteMemoryBridge(
        tmp_db, embedding_registry=embeddings, lancedb=lancedb,
        semantic_search_enabled=True,
    )
    promoter = FactPromoter(tmp_db, lancedb=lancedb)

    fact_id = await remember_fact(
        bridge,
        promoter,
        _FACT_CONTENT,
        source_type="agent_self",
        embedding_registry=embeddings,
    )

    rows = await tmp_db.fetch_all(
        "SELECT embedding, embedding_model FROM committed_facts WHERE fact_id = ?",
        (fact_id,),
    )
    assert rows, "fact must be committed"
    blob = rows[0]["embedding"]
    model = rows[0]["embedding_model"]
    assert blob, f"committed fact embedding BLOB must be NON-EMPTY; got {blob!r}"
    assert len(blob) > 0, "embedding BLOB must contain bytes"
    assert model, f"embedding_model must be non-empty; got {model!r}"


async def test_force_promoted_fact_is_semantically_recallable(
    tmp_db: DbPool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A remembered fact must be found via the SEMANTIC (LanceDB) path, not just FTS."""
    monkeypatch.setattr(
        TestModeGuard, "assert_not_test_mode", staticmethod(lambda _op: None)
    )

    embeddings = EmbeddingRegistry()
    lancedb = LanceDBAdapter(data_dir=tmp_path / "lancedb")
    bridge = SqliteMemoryBridge(
        tmp_db, embedding_registry=embeddings, lancedb=lancedb,
        semantic_search_enabled=True,
    )
    promoter = FactPromoter(tmp_db, lancedb=lancedb)

    fact_id = await remember_fact(
        bridge,
        promoter,
        _FACT_CONTENT,
        source_type="agent_self",
        embedding_registry=embeddings,
    )

    # SEMANTIC path only — this returns None on failure, [] on empty, records on hit.
    # Pre-fix the LanceDB table is never created → search returns [] → assertion fails.
    semantic = await semantic_recall(
        tmp_db, embeddings, lancedb, _FACT_CONTENT, 5
    )
    assert semantic, (
        "semantic recall must return the remembered fact (vector present in LanceDB); "
        f"got {semantic!r}"
    )
    assert any(r.fact_id == fact_id for r in semantic), (
        f"semantic path must surface the remembered fact; "
        f"got {[r.fact_id for r in (semantic or [])]}"
    )
