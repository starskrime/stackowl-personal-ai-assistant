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
        # MEM-1 (F073) — present once the 0062 migration runs; legacy SELECTs
        # without the column .get() to the conservative one-off floor.
        reinforcement_count=int(row.get("reinforcement_count", 0) or 0),
        # Phase 2 — present once the 0085 migration runs; .get() so a SELECT
        # that hasn't been updated yet degrades to unscoped, never KeyErrors.
        scope_key=row.get("scope_key"),
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
                      cf.trust, cf.reinforcement_count, cf.scope_key
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


async def staged_recall(
    db: DbPool, query: str, limit: int
) -> list[MemoryRecord]:
    """Substring recall over ``staged_facts`` — ESC-69's interim.

    WHY A SCAN AND NOT AN INDEX. There is no FTS table on ``staged_facts`` and
    building one means a migration. The table holds 361 rows (measured
    2026-08-30), so a LIKE scan is free at this size and reversible in one commit
    if the store is retired — which is the stated plan. An index would be the
    right answer at a hundred times the size and the wrong answer at this one.

    WHY IT RETURNS RAW TURNS, stated rather than hidden. Staged content is
    conversation text, not distilled facts, because the extractor that used to
    promote staged -> committed was retired (D08.1 R5Q18). Recall reading only
    ``committed_facts`` — 0 rows — is why 414 searches returned 0 archive hits.
    This makes what exists reachable; it does not make it tidy.

    ONLY status='staged'. A rejected fact is a decision, not a memory, and
    surfacing it would resurrect something the platform already declined.
    """
    needle = (query or "").strip()
    if not needle:
        # An empty needle with LIKE '%%' matches EVERY row. Returning the whole
        # store on a blank query is the shape that turns a search into a dump.
        return []
    # `%` and `_` are LIKE wildcards: unescaped, "a_c" would match "abc" and a
    # lone "%" would match everything. ESCAPE makes them literal.
    escaped = needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    rows = await db.fetch_all(
        # COALESCE on embedding_model: MemoryRecord requires a string and a staged
        # row may carry an embedding with no model recorded. "" is the honest
        # value for "not stated" — inventing a model name would be worse.
        """SELECT fact_id, content, embedding,
                  COALESCE(embedding_model, '') AS embedding_model,
                  staged_at AS committed_at, source_type, source_ref,
                  '[]' AS tags, COALESCE(trust, 'untrusted') AS trust,
                  COALESCE(reinforcement_count, 0) AS reinforcement_count, scope_key
           FROM staged_facts
           WHERE status = 'staged' AND content LIKE ? ESCAPE '\\'
           ORDER BY staged_at DESC
           LIMIT ?""",
        (f"%{escaped}%", limit),
    )
    log.memory.debug(
        "[memory] sqlite_helpers.staged_recall: exit",
        extra={"_fields": {"n_results": len(rows), "limit": limit}},
    )
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
                   committed_at, source_type, source_ref, tags, trust,
                   reinforcement_count, scope_key
            FROM committed_facts
            WHERE fact_id IN ({placeholders})""",
        tuple(fact_ids),
    )
    by_id = {r["fact_id"]: row_to_record(r) for r in rows}
    return [by_id[fid] for fid in fact_ids if fid in by_id]


# `semantic_recall` stood here: embed the query, ANN-search LanceDB, then hydrate
# the hits from SQLite by fact_id. It went with the vector store in D08.2 — the
# hydration read committed_facts, which has 0 rows and no writer since seam 3
# pass 4, so every ANN hit resolved to nothing and fell through to FTS anyway.


def filter_by_scope(
    records: list[MemoryRecord], scope_key: str | None
) -> list[MemoryRecord]:
    """Phase 2 (coding-capability build plan) — POST-filter a recall candidate
    set by scope. ``scope_key=None`` (the default) is a no-op — byte-identical
    to every pre-Phase-2 call. Otherwise keeps a record only when its OWN
    ``scope_key`` matches the requested one, or is ``None`` (global facts stay
    visible in every scope, never hidden by a scoped query)."""
    if scope_key is None:
        return records
    return [r for r in records if r.scope_key is None or r.scope_key == scope_key]


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
        # Phase 2 — present once the 0085 migration runs; .get() so a SELECT
        # that hasn't been updated yet degrades to unscoped, never KeyErrors.
        scope_key=row.get("scope_key"),
    )
