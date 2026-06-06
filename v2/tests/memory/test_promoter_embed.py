"""Task 5 — FactPromoter computes missing embedding at promote time.

A miner-staged fact (embedding=None) must become semantically recallable after
force_promote().  Without an embedding_registry the promoter must succeed FTS-only
(fail-open, no crash).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from stackowl.db.pool import DbPool
from stackowl.memory.fact_promoter import FactPromoter
from stackowl.memory.sqlite_helpers import pack_embedding

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class _StubProvider:
    model_name = "stub-embed"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3] for _ in texts]


class _StubEmbReg:
    def get(self) -> _StubProvider:
        return _StubProvider()


class _SpyLance:
    """Tracks upsert calls so we can assert a vector was computed + stored."""

    def __init__(self) -> None:
        self.upserts: list[tuple[str, list[float]]] = []

    async def upsert(
        self, fid: str, vec: list[float], meta: dict[str, object]
    ) -> None:
        self.upserts.append((fid, vec))


# ---------------------------------------------------------------------------
# Helper — mirrors _insert_staged from test_story_6_3
# ---------------------------------------------------------------------------


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
# Tests
# ---------------------------------------------------------------------------


async def test_promote_computes_embedding_when_missing(tmp_db: DbPool) -> None:
    """force_promote on a fact with no embedding must embed it and upsert to LanceDB."""
    fid = str(uuid.uuid4())
    await _insert_staged(
        tmp_db,
        fact_id=fid,
        content="the user prefers tabs",
        embedding=None,
    )
    lance = _SpyLance()
    promoter = FactPromoter(tmp_db, lancedb=lance, embedding_registry=_StubEmbReg())  # type: ignore[arg-type]
    assert await promoter.force_promote(fid) is True
    assert lance.upserts, "expected a vector to be upserted to LanceDB"
    upserted_fid, upserted_vec = lance.upserts[0]
    assert upserted_fid == fid
    assert upserted_vec == [0.1, 0.2, 0.3]


async def test_promote_failopen_without_registry(tmp_db: DbPool) -> None:
    """force_promote without an embedding_registry must succeed FTS-only — no crash."""
    fid = str(uuid.uuid4())
    await _insert_staged(
        tmp_db,
        fact_id=fid,
        content="the user dislikes dark mode",
        embedding=None,
    )
    # No embedding_registry, no lancedb → FTS-only path.
    promoter = FactPromoter(tmp_db)
    assert await promoter.force_promote(fid) is True
    # Fact must appear in committed_facts (FTS-only).
    rows = await tmp_db.fetch_all(
        "SELECT fact_id FROM committed_facts WHERE fact_id = ?", (fid,)
    )
    assert rows, "fact must be committed even without an embedding"
