"""Helpers for SqliteMemoryBridge: BLOB packing, ISO parsing, row mapping, recall."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import numpy as np

from stackowl.infra.observability import log
from stackowl.memory.models import MemoryRecord, StagedFact

if TYPE_CHECKING:  # pragma: no cover — typing-only imports
    from stackowl.db.pool import DbPool
    from stackowl.embeddings.registry import EmbeddingRegistry
    from stackowl.memory.lancedb_adapter import LanceDBAdapter


def pack_embedding(embedding: list[float] | None) -> bytes | None:
    """Pack a float vector as little-endian float32 bytes for BLOB storage."""
    if embedding is None:
        return None
    return np.array(embedding, dtype="<f4").tobytes()


def unpack_embedding(blob: bytes | None) -> list[float]:
    """Unpack a float32 little-endian BLOB back into a ``list[float]``."""
    if not blob:
        return []
    arr = np.frombuffer(blob, dtype="<f4")
    return [float(x) for x in arr]


def cosine_similarity(a: list[float] | None, b: list[float] | None) -> float | None:
    """Cosine similarity of two vectors in ``[-1.0, 1.0]``.

    Returns ``None`` when similarity is undefined — either operand is missing,
    empty, length-mismatched, or a zero vector — so callers can fall back rather
    than treat a degenerate comparison as a match.
    """
    if not a or not b or len(a) != len(b):
        return None
    va = np.asarray(a, dtype="<f4")
    vb = np.asarray(b, dtype="<f4")
    na = float(np.linalg.norm(va))
    nb = float(np.linalg.norm(vb))
    if na == 0.0 or nb == 0.0:
        return None
    return float(np.dot(va, vb) / (na * nb))


def parse_iso(value: str) -> datetime:
    """Parse an ISO-8601 timestamp, defaulting to UTC for naive values."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError) as exc:
        log.memory.warning(
            "[memory] sqlite_helpers.parse_iso: invalid timestamp, defaulting to now()",
            exc_info=exc,
            extra={"_fields": {"value": str(value)[:50]}},
        )
        return datetime.now(UTC)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def row_to_record(row: dict[str, Any]) -> MemoryRecord:
    """Map a ``committed_facts`` row dict to a :class:`MemoryRecord`."""
    committed_at = parse_iso(row["committed_at"])
    tags_raw = row.get("tags") or "[]"
    try:
        tags = json.loads(tags_raw)
        if not isinstance(tags, list):
            tags = []
    except (json.JSONDecodeError, TypeError) as exc:
        log.memory.warning(
            "[memory] sqlite_helpers.row_to_record: invalid tags JSON",
            exc_info=exc,
            extra={"_fields": {"fact_id": row.get("fact_id")}},
        )
        tags = []
    return MemoryRecord(
        fact_id=row["fact_id"],
        content=row["content"],
        embedding=unpack_embedding(row["embedding"]),
        embedding_model=row["embedding_model"],
        committed_at=committed_at,
        source_type=row["source_type"],
        source_ref=row["source_ref"],
        tags=list(tags),
        trust=row.get("trust", "untrusted"),
    )


def _sanitize_fts_query(query: str) -> str:
    """Convert a free-text query into a safe FTS5 MATCH expression.

    FTS5 treats ``,`` ``:`` ``(`` ``)`` ``*`` ``-`` ``+`` ``"`` and other
    punctuation as operators; passing raw user text causes parse errors.
    We extract Unicode word tokens (``\\p{L}\\p{N}``-equivalent via
    ``str.isalnum``) and join them as a quoted disjunction:
    ``"foo" OR "bar" OR "baz"``. Empty input returns an empty string —
    the caller short-circuits before querying.
    """
    import re
    tokens = re.findall(r"[^\W_]+", query, flags=re.UNICODE)
    if not tokens:
        return ""
    # Quote each token to neutralize any remaining FTS5 metasyntax; cap at 16
    # terms so a giant prompt doesn't blow the query parser.
    return " OR ".join(f'"{t}"' for t in tokens[:16])


async def fts_recall(
    db: DbPool, query: str, limit: int
) -> list[MemoryRecord]:
    """FTS5 BM25 recall over ``committed_facts``. Returns ``[]`` on parse error."""
    fts_query = _sanitize_fts_query(query)
    if not fts_query:
        return []
    try:
        rows = await db.fetch_all(
            """SELECT cf.fact_id, cf.content, cf.embedding, cf.embedding_model,
                      cf.committed_at, cf.source_type, cf.source_ref, cf.tags,
                      cf.trust
               FROM committed_facts_fts fts
               JOIN committed_facts cf ON cf.rowid = fts.rowid
               WHERE committed_facts_fts MATCH ?
               ORDER BY bm25(committed_facts_fts)
               LIMIT ?""",
            (fts_query, limit),
        )
    except Exception as exc:
        # FTS5 still rejected the sanitized query (rare) — fail soft.
        log.memory.warning(
            "[memory] sqlite_helpers.fts_recall: FTS5 query failed — returning empty",
            exc_info=exc,
            extra={"_fields": {"query_len": len(query), "fts_query_len": len(fts_query)}},
        )
        return []
    return [row_to_record(row) for row in rows]


async def fetch_committed_by_ids(
    db: DbPool, fact_ids: list[str]
) -> list[MemoryRecord]:
    """Fetch committed_facts rows for the given fact_ids, preserving input order."""
    if not fact_ids:
        return []
    placeholders = ",".join(["?"] * len(fact_ids))
    rows = await db.fetch_all(
        f"""SELECT fact_id, content, embedding, embedding_model,
                   committed_at, source_type, source_ref, tags, trust
            FROM committed_facts
            WHERE fact_id IN ({placeholders})""",
        tuple(fact_ids),
    )
    by_id = {r["fact_id"]: row_to_record(r) for r in rows}
    return [by_id[fid] for fid in fact_ids if fid in by_id]


async def semantic_recall(
    db: DbPool,
    embeddings: EmbeddingRegistry,
    lancedb: LanceDBAdapter,
    query: str,
    limit: int,
) -> list[MemoryRecord] | None:
    """Try LanceDB-backed recall.

    Returns ``None`` on any failure so the caller can fall back to FTS5.
    Returns ``[]`` when LanceDB returns no hits.
    """
    try:
        vectors = await embeddings.get().embed([query])
    except Exception as exc:
        # B5
        log.memory.warning(
            "[memory] sqlite_helpers.semantic_recall: embed failed",
            exc_info=exc,
            extra={"_fields": {"query_len": len(query)}},
        )
        return None
    if not vectors or not vectors[0]:
        log.memory.warning(
            "[memory] sqlite_helpers.semantic_recall: empty embedding",
            extra={"_fields": {"query_len": len(query)}},
        )
        return None
    try:
        hits = await lancedb.search(vectors[0], limit=limit)
    except Exception as exc:
        # B5 — never crash recall on ANN failure
        log.memory.warning(
            "[memory] sqlite_helpers.semantic_recall: lancedb search failed",
            exc_info=exc,
            extra={"_fields": {"query_len": len(query)}},
        )
        return None
    if not hits:
        return []
    return await fetch_committed_by_ids(db, [h.fact_id for h in hits])


def row_to_staged(row: dict[str, Any]) -> StagedFact:
    """Map a ``staged_facts`` row dict to a :class:`StagedFact`."""
    embedding_blob = row.get("embedding")
    embedding = unpack_embedding(embedding_blob) if embedding_blob else None
    return StagedFact(
        fact_id=row["fact_id"],
        content=row["content"],
        source_type=row["source_type"],
        source_ref=row["source_ref"],
        confidence=float(row["confidence"]),
        staged_at=parse_iso(row["staged_at"]),
        reinforcement_count=int(row["reinforcement_count"]),
        status=row["status"],
        embedding=embedding,
        embedding_model=row.get("embedding_model"),
        # Task 8 promoter SELECTs don't yet include trust; .get() avoids KeyError.
        # Once Task 8 adds trust to those SELECTs, this will also read the real value.
        trust=row.get("trust", "untrusted"),
    )
