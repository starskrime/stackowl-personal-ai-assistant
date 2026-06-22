"""F066 (P1) — LanceDB table dim locked to first vector → model/dim swap.

The committed_facts table pins its vector dim to the FIRST upsert's length. A
later different-dim upsert is rejected by Arrow and (in the promoter) SWALLOWED
by B5 — every new fact silently degrades to FTS-only with NO signal.

Fix: detect the mismatch at the write seam and raise the typed
``EmbeddingDimensionMismatch`` SIGNAL (not the doomed Arrow throw). The adapter
catches it loudly. A reindex phase rebuilds the table at the new dim from the
SQLite source of truth and rewrites the sidecar.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stackowl.config.test_mode import TestModeGuard
from stackowl.memory.lancedb_adapter import LanceDBAdapter
from stackowl.memory.lancedb_helpers import (
    EmbeddingDimensionMismatch,
    read_corpus_identity,
    sync_recreate_table,
    sync_upsert,
    write_corpus_identity,
)


def _connect(data_dir: Path):  # type: ignore[no-untyped-def]
    import lancedb

    data_dir.mkdir(parents=True, exist_ok=True)
    return lancedb.connect(str(data_dir))


def test_sync_upsert_raises_typed_signal_on_dim_swap(tmp_path: Path) -> None:
    """A dim-768 upsert into a dim-384 corpus raises the typed signal, NOT Arrow."""
    conn = _connect(tmp_path / "lance")
    sync_upsert(conn, "f1", [0.1] * 384, {"embedding_model": "hash-v1-384d"})
    write_corpus_identity(conn, "hash-v1-384d", 384)

    with pytest.raises(EmbeddingDimensionMismatch):
        sync_upsert(conn, "f2", [0.2] * 768, {"embedding_model": "all-mpnet-768d"})


@pytest.mark.asyncio
async def test_adapter_upsert_swallows_mismatch_loudly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capture_logs: list
) -> None:
    """The adapter catches the typed signal (loud WARN), never crashes the caller."""
    monkeypatch.setattr(TestModeGuard, "assert_not_test_mode", staticmethod(lambda _op: None))
    lancedb = LanceDBAdapter(data_dir=tmp_path / "lance")
    await lancedb.upsert("f1", [0.1] * 384, {"embedding_model": "hash-v1-384d"})
    conn = lancedb._connect()  # type: ignore[attr-defined]
    write_corpus_identity(conn, "hash-v1-384d", 384)

    # Must NOT raise — deferred to reindex; the fact is already in SQLite+FTS.
    await lancedb.upsert("f2", [0.2] * 768, {"embedding_model": "all-mpnet-768d"})

    blob = " ".join(str(rec) for rec in capture_logs)
    assert "768" in blob or "mismatch" in blob.lower() or "drift" in blob.lower(), (
        f"dim-swap must log loudly; logs={blob[:500]}"
    )


def test_recreate_table_changes_dim(tmp_path: Path) -> None:
    """sync_recreate_table drops + recreates the table at the TARGET dim."""
    conn = _connect(tmp_path / "lance")
    sync_upsert(conn, "f1", [0.1] * 384, {"embedding_model": "hash-v1-384d"})
    # Recreate at 768 and write a 768 vector.
    sync_recreate_table(conn, 768)
    sync_upsert(conn, "f2", [0.2] * 768, {"embedding_model": "all-mpnet-768d"})
    write_corpus_identity(conn, "all-mpnet-768d", 768)
    assert read_corpus_identity(conn) == ("all-mpnet-768d", 768)
    # The 768 vector is searchable under the new dim.
    hits = conn.open_table("committed_facts").search([0.2] * 768).limit(5).to_list()
    assert any(h["fact_id"] == "f2" for h in hits)


@pytest.mark.asyncio
async def test_reindex_phase_cures_drift(
    tmp_path: Path, tmp_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The dream-worker reembed phase rebuilds the table at the active dim from SQLite."""
    from datetime import UTC, datetime

    monkeypatch.setattr(TestModeGuard, "assert_not_test_mode", staticmethod(lambda _op: None))

    # Seed a committed fact in SQLite (the SoT) embedded under the OLD model.
    import numpy as np

    old_vec = np.array([0.1] * 384, dtype="<f4").tobytes()
    await tmp_db.execute(
        """INSERT INTO committed_facts
               (fact_id, content, embedding, embedding_model, source_type,
                source_ref, tags, trust, committed_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "f1", "the user lives in Baku", old_vec, "hash-v1-384d",
            "conversation_fact", "s1", "[]", "self", datetime.now(UTC).isoformat(),
        ),
    )

    lancedb = LanceDBAdapter(data_dir=tmp_path / "lance")
    await lancedb.upsert("f1", [0.1] * 384, {"embedding_model": "hash-v1-384d"})
    conn = lancedb._connect()  # type: ignore[attr-defined]
    write_corpus_identity(conn, "hash-v1-384d", 384)

    # A fake provider that re-embeds at a NEW dim/model.
    from stackowl.memory.dream_worker_helpers import reembed_committed_facts

    async def _embed(texts: list[str]) -> list[list[float]]:
        return [[0.5] * 768 for _ in texts]

    written = await reembed_committed_facts(
        tmp_db, lancedb, embed=_embed, active_model="all-mpnet-768d", active_dim=768
    )
    assert written == 1
    assert read_corpus_identity(conn) == ("all-mpnet-768d", 768)
    hits = conn.open_table("committed_facts").search([0.5] * 768).limit(5).to_list()
    assert any(h["fact_id"] == "f1" for h in hits)


@pytest.mark.asyncio
async def test_reindex_heals_vectorless_legacy_facts(
    tmp_path: Path, tmp_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Phase-L guard fix: a committed fact with NO embedding blob (legacy/untagged
    corpus) still re-embeds from the SQLite SoT text and tags the corpus — the case
    the old `count_committed_with_vectors` guard skipped, leaving drift forever."""
    from datetime import UTC, datetime

    monkeypatch.setattr(TestModeGuard, "assert_not_test_mode", staticmethod(lambda _op: None))

    # Committed fact with an EMPTY embedding blob (legacy/untagged): the column is
    # NOT NULL, so the "vectorless" case is a zero-length blob — exactly what the
    # old count_committed_with_vectors (LENGTH(embedding) > 0) guard skipped.
    await tmp_db.execute(
        """INSERT INTO committed_facts
               (fact_id, content, embedding, embedding_model, source_type,
                source_ref, tags, trust, committed_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "f1", "the user lives in Baku", b"", "legacy",
            "conversation_fact", "s1", "[]", "self", datetime.now(UTC).isoformat(),
        ),
    )

    from stackowl.memory.dream_worker_helpers import (
        count_committed_facts,
        count_committed_with_vectors,
        reembed_committed_facts,
    )

    # The OLD guard would skip (no vectors); the NEW guard gates on facts.
    assert await count_committed_with_vectors(tmp_db) == 0
    assert await count_committed_facts(tmp_db) == 1

    lancedb = LanceDBAdapter(data_dir=tmp_path / "lance")

    async def _embed(texts: list[str]) -> list[list[float]]:
        return [[0.5] * 768 for _ in texts]

    written = await reembed_committed_facts(
        tmp_db, lancedb, embed=_embed, active_model="all-mpnet-768d", active_dim=768
    )
    assert written == 1
    conn = lancedb._connect()  # type: ignore[attr-defined]
    assert read_corpus_identity(conn) == ("all-mpnet-768d", 768)
