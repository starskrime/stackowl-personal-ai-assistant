"""Helpers for SqliteMemoryBridge: BLOB packing, ISO parsing, row mapping, recall."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import numpy as np

from stackowl.infra.observability import log
from stackowl.memory.models import MemoryRecord, StagedFact
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID

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
           WHERE owner_id = ? AND status = 'staged' AND content LIKE ? ESCAPE '\\'
           ORDER BY staged_at DESC
           LIMIT ?""",
        (DEFAULT_PRINCIPAL_ID, f"%{escaped}%", limit),
    )
    log.memory.debug(
        "[memory] sqlite_helpers.staged_recall: exit",
        extra={"_fields": {"n_results": len(rows), "limit": limit}},
    )
    return [row_to_record(row) for row in rows]


#: Cosine below which a semantic hit is not a hit.
#:
#: DERIVED, NOT PICKED. The substring scan returned ZERO when nothing matched,
#: which was — by accident — the only relevance threshold in the whole recall
#: path. Cosine top-k always returns k, so without a floor every turn would carry
#: five "memories" however unrelated, and the caller cannot tell a 0.95 from a
#: 0.05 because MemoryRecord is frozen with extra="forbid" and has nowhere to
#: hold a score. For 384-dimension unit vectors two random directions have cosine
#: ~0 with standard deviation 1/sqrt(384) = 0.051, so 0.25 is roughly five
#: standard deviations above chance: comfortably "not noise" without demanding
#: near-paraphrase.
_MIN_SEMANTIC_SIMILARITY = 0.25


async def staged_semantic_recall(
    db: DbPool,
    query_embedding: list[float] | None,
    limit: int,
    *,
    embedding_model: str | None = None,
    min_similarity: float = _MIN_SEMANTIC_SIMILARITY,
) -> list[MemoryRecord]:
    """Semantic recall over ``staged_facts`` embeddings.

    WHY THIS EXISTS. :func:`staged_recall` matches ``content LIKE '%<the ENTIRE
    query>%'`` — the whole question as one literal phrase. MEASURED 2026-09-03
    against the live archive: "jobmarket" returned 5 rows, "jobmarket agent"
    returned 0, "agents" returned 5, and "what agents do I have" returned 0. A
    single keyword works and ANY natural question returns nothing, while a fact
    reading "The jobmarket scout is the existing daily 2 PM CT agent" sat
    invisible to the last of those. Since ``committed_facts`` has held 0 rows
    since migration 0112, that scan IS the whole archive path.

    THE INTERIM'S PREMISE EXPIRED. The substring scan was ESC-69's stopgap,
    written when ``staged_facts`` carried no vectors at all. A separate fix on
    2026-08-25 started writing them — "this bridge held an embedding registry and
    never used it" — and recall was never switched over. MEASURED: 205 of 230
    staged rows (89%) now hold a 384-dimension all-MiniLM-L6-v2 vector.

    IT DOES NOT REPLACE THE SCAN. 25 rows carry no vector, and dropping the
    substring path would make exactly those unreachable — the "made it
    unreachable and called it a cleanup" mistake ESC-69 exists to correct. The
    caller runs this first and tops up with the scan.

    Copies :mod:`stackowl.learning.lessons_store`'s proven shape, which
    ``recall``'s own docstring names as the replacement pattern, including its two
    safety properties: a query only ever sees rows of its OWN embedding dimension
    (a different dimension is a different space, not a near miss), and a failed
    embedder yields no hits rather than an exception on the turn path.

    NO CACHE, deliberately. lessons_store caches because it scans thousands of
    rows; this table is 230 and is WRITTEN DURING A TURN, so a cache would need an
    invalidation story across the gateway/core split to avoid serving a memory the
    user just created. At this size the scan is free and correctness is free with
    it.

    Args:
        db: The pool.
        query_embedding: The question's vector, or None when embedding failed.
        limit: Maximum hits.

    Returns:
        Records ordered most-similar first; empty on a missing vector, an empty
        corpus, or an embedder-dimension mismatch. Never raises.
    """
    # 1. ENTRY
    log.memory.debug(
        "[memory] sqlite_helpers.staged_semantic_recall: entry",
        extra={"_fields": {"dims": len(query_embedding or []), "limit": limit}},
    )
    # 2. DECISION — an embedder that failed hands back None/[]; searching on it
    # must not be a crash on the turn path.
    if not query_embedding:
        return []
    query = np.asarray(query_embedding, dtype=np.float32)
    dim = int(query.shape[0])
    try:
        rows = await db.fetch_all(
            """SELECT fact_id, content, embedding,
                      COALESCE(embedding_model, '') AS embedding_model,
                      staged_at AS committed_at, source_type, source_ref,
                      '[]' AS tags, COALESCE(trust, 'untrusted') AS trust,
                      COALESCE(reinforcement_count, 0) AS reinforcement_count,
                      scope_key
               FROM staged_facts
               WHERE owner_id = ? AND status = 'staged'
                 AND embedding IS NOT NULL AND length(embedding) > 0
                 AND (? = '' OR embedding_model = ?)""",
            (DEFAULT_PRINCIPAL_ID, embedding_model or "", embedding_model or ""),
        )
    except Exception as exc:  # noqa: BLE001 — recall may never cost the turn
        log.memory.warning(
            "[memory] sqlite_helpers.staged_semantic_recall: corpus read failed "
            "— falling back to the substring scan alone",
            exc_info=exc,
        )
        return []

    usable: list[dict[str, Any]] = []
    vectors: list[np.ndarray] = []
    for row in rows:
        vec = np.frombuffer(row["embedding"], dtype="<f4")
        # A DIFFERENT DIMENSION IS A DIFFERENT SPACE. Comparing across would
        # return confident nonsense; skipping returns an honest miss.
        if int(vec.shape[0]) != dim:
            continue
        usable.append(row)
        vectors.append(vec)
    if not usable:
        if rows:
            log.memory.warning(
                "[memory] sqlite_helpers.staged_semantic_recall: no staged row "
                "shares the query's embedding dimension — embedder drift, "
                "returning no hits rather than wrong ones",
                extra={"_fields": {"query_dim": dim, "rows_scanned": len(rows)}},
            )
        return []

    matrix = np.stack(vectors)
    # Squared L2 in full: ||q||^2 + ||e||^2 - 2 q.e. The identity with cosine
    # holds only for unit-norm vectors, which is a property of today's embedder
    # rather than of this schema — so the full form, as lessons_store uses.
    distances = (
        float(query @ query) + np.einsum("ij,ij->i", matrix, matrix)
        - 2.0 * (matrix @ query)
    )
    np.maximum(distances, 0.0, out=distances)
    top = np.argsort(distances)[:limit]
    # THE FLOOR. Squared L2 relates to cosine as d = 2(1 - cos) for unit vectors,
    # so cos = 1 - d/2. Applied AFTER the sort so the cut is on the best
    # candidates rather than on an arbitrary prefix.
    hits = [
        row_to_record(usable[i])
        for i in top
        if (1.0 - float(distances[i]) / 2.0) >= min_similarity
    ]
    # 4. EXIT
    log.memory.debug(
        "[memory] sqlite_helpers.staged_semantic_recall: exit",
        extra={"_fields": {"n_hits": len(hits), "scanned": len(usable), "dim": dim}},
    )
    return hits


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
