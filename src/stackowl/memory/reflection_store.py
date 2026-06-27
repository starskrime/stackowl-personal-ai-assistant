"""ReflectionStore — persistence for Reflexion-style learning artifacts.

Dedicated table (migration 0030) — kept separate from staged_facts because
reflections are *learning* artifacts (telemetry about how the agent thought),
not knowledge facts. Operator-approved decision per the Commit 2 audit.

POSITIVE-ONLY LEARNING (operator directive): a reflection is written exactly
when the critic scored an outcome as a SUCCESS with ``quality_score >= 0.6`` and
no ``failure_class``. Failures and low-quality outcomes are skipped — the
platform remembers what worked, never "this failed / I can't".
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from stackowl.db.pool import DbPool
from stackowl.infra.observability import log
from stackowl.memory.outcome_store import TaskOutcome
from stackowl.memory.sqlite_helpers import (
    cosine_similarity,
    pack_embedding,
    unpack_embedding,
)
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID, OwnedRepository

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.embeddings.registry import EmbeddingRegistry


@dataclass(frozen=True)
class Reflection:
    """Read-side projection of one reflections row."""

    reflection_id: int
    trace_id: str
    owl_name: str
    summary: str
    suggested_strategy: str
    failure_class: str | None
    quality_score: float | None
    embedding: list[float] | None
    embedding_model: str | None
    created_at: float


# Filter applied to task_outcomes when picking the next batch to reflect on.
#
# F-48 (ACCEPTED BY DIRECTIVE — do NOT "fix" this): an audit flagged that
# reflections are written only for successes and suggested also remembering
# FAILURES. That suggestion is intentionally REJECTED. POSITIVE-ONLY LEARNING is
# a hard operator product directive: the platform persists ONLY "what worked"
# and must NEVER accumulate "this failed / I can't" memories into the store. The
# `success = 1 AND failure_class IS NULL AND quality_score >= 0.6` predicate
# below is therefore correct and load-bearing — leave it as-is.
#
# Rationale (memory): feedback_positive_only_learning. Within-turn FAILURE
# AWARENESS (degradation signal that is never persisted) is handled elsewhere
# (see classify._gather_recent_reflections F-49), which is the only honest place
# a failure may surface — transiently, to the current turn, never written here.
_HIGH_QUALITY_THRESHOLD = 0.6

_LIST_PENDING_SQL = f"""
SELECT o.trace_id, o.session_id, o.owl_name, o.channel,
       o.success, o.latency_ms, o.tool_call_count, o.failure_class,
       o.quality_score, o.step_durations, o.input_text, o.response_text,
       o.captured_at, o.scored_at, o.outcome_id, o.tool_sequence,
       o.dna_snapshot
FROM task_outcomes o
LEFT JOIN reflections r ON r.trace_id = o.trace_id AND r.owner_id = o.owner_id
WHERE o.owner_id = ?
  AND r.reflection_id IS NULL
  AND o.quality_score IS NOT NULL
  AND o.failure_class IS NULL
  AND o.success = 1
  AND o.quality_score >= {_HIGH_QUALITY_THRESHOLD}
ORDER BY o.captured_at ASC
LIMIT ?
"""

_INSERT_SQL = """
INSERT INTO reflections (
    trace_id, owl_name, summary, suggested_strategy,
    failure_class, quality_score, embedding, embedding_model, created_at,
    owner_id
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(trace_id) DO NOTHING
"""


class ReflectionStore(OwnedRepository):
    """Async SQLite wrapper for the reflections table (migration 0030).

    Owner-scoped: reads/writes are constrained to ``owner_id`` (defaults to the
    single-user :data:`DEFAULT_PRINCIPAL_ID`, so existing behavior is unchanged).
    """

    _table = "reflections"

    def __init__(self, db: DbPool, owner_id: str = DEFAULT_PRINCIPAL_ID) -> None:
        super().__init__(db, owner_id)
        log.memory.debug("[reflections] store.init: ready")

    async def list_pending(self, limit: int = 10) -> list[TaskOutcome]:
        """Return task_outcomes that are scored, eligible-for-reflection, and unreflected.

        Eligibility (positive-only): success = 1 AND failure_class IS NULL AND
        quality_score >= 0.6. Failures and low-quality outcomes are intentionally
        skipped — the platform learns only from what worked.
        """
        # 1. ENTRY
        log.memory.debug(
            "[reflections] list_pending: entry",
            extra={"_fields": {"limit": limit, "high_quality_threshold": _HIGH_QUALITY_THRESHOLD}},
        )
        # 3. STEP — LEFT JOIN against reflections gives us only unreflected rows
        rows = await self._db.fetch_all(_LIST_PENDING_SQL, (self._owner_id, limit))
        # Hand-build TaskOutcome objects so we don't depend on outcome_store's
        # internal _row_to_outcome (different SQL alias shape).
        results: list[TaskOutcome] = []
        for r in rows:
            import json as _json

            try:
                step_durations = _json.loads(str(r.get("step_durations") or "{}"))
            except _json.JSONDecodeError:
                step_durations = {}
            qs = r.get("quality_score")
            sa = r.get("scored_at")
            seq_raw = r.get("tool_sequence") or "[]"
            try:
                seq_list = _json.loads(str(seq_raw))
                if not isinstance(seq_list, list):
                    seq_list = []
            except _json.JSONDecodeError:
                seq_list = []
            dna_raw = r.get("dna_snapshot") or "{}"
            try:
                dna_dict = _json.loads(str(dna_raw))
                if not isinstance(dna_dict, dict):
                    dna_dict = {}
                dna_snapshot = {
                    str(k): float(v) for k, v in dna_dict.items()
                    if isinstance(v, int | float)
                }
            except (_json.JSONDecodeError, TypeError, ValueError):
                dna_snapshot = {}
            results.append(TaskOutcome(
                outcome_id=int(str(r["outcome_id"])),
                trace_id=str(r["trace_id"]),
                session_id=str(r["session_id"]),
                owl_name=str(r["owl_name"]),
                channel=str(r["channel"]),
                success=bool(r["success"]),
                latency_ms=float(str(r["latency_ms"])),
                tool_call_count=int(str(r["tool_call_count"])),
                failure_class=str(r["failure_class"]) if r.get("failure_class") else None,
                quality_score=float(str(qs)) if qs is not None else None,
                step_durations=step_durations,
                input_text=str(r.get("input_text", "")),
                response_text=str(r.get("response_text", "")),
                captured_at=float(str(r["captured_at"])),
                scored_at=float(str(sa)) if sa is not None else None,
                tool_sequence=tuple(str(t) for t in seq_list),
                dna_snapshot=dna_snapshot,
            ))
        # 4. EXIT
        log.memory.debug(
            "[reflections] list_pending: exit",
            extra={"_fields": {"limit": limit, "n_pending": len(results)}},
        )
        return results

    async def write(
        self,
        *,
        trace_id: str,
        owl_name: str,
        summary: str,
        suggested_strategy: str,
        failure_class: str | None,
        quality_score: float | None,
        embedding: list[float] | None,
        embedding_model: str | None,
    ) -> None:
        """Insert a new reflection. Idempotent on trace_id."""
        # 1. ENTRY
        log.memory.debug(
            "[reflections] write: entry",
            extra={"_fields": {
                "trace_id": trace_id, "owl_name": owl_name,
                "has_embedding": embedding is not None,
                "summary_len": len(summary),
            }},
        )
        embedding_blob = pack_embedding(embedding) if embedding else None
        # 3. STEP
        await self._db.execute(
            _INSERT_SQL,
            (
                trace_id, owl_name, summary[:4000], suggested_strategy[:4000],
                failure_class, quality_score, embedding_blob, embedding_model,
                time.time(), self._owner_id,
            ),
        )
        # 4. EXIT
        log.memory.info(
            "[reflections] write: stored",
            extra={"_fields": {"trace_id": trace_id, "owl_name": owl_name}},
        )

    async def get_by_trace_id(self, trace_id: str) -> Reflection | None:
        # 1. ENTRY
        log.memory.debug(
            "[reflections] get_by_trace_id: entry",
            extra={"_fields": {"trace_id": trace_id}},
        )
        rows = await self._db.fetch_all(
            """SELECT reflection_id, trace_id, owl_name, summary, suggested_strategy,
                      failure_class, quality_score, embedding, embedding_model, created_at
               FROM reflections WHERE owner_id = ? AND trace_id = ?""",
            (self._owner_id, trace_id),
        )
        # 2. DECISION + 4. EXIT
        if not rows:
            log.memory.debug(
                "[reflections] get_by_trace_id: exit — miss",
                extra={"_fields": {"trace_id": trace_id}},
            )
            return None
        r = rows[0]
        ref = _row_to_reflection(r)
        log.memory.debug(
            "[reflections] get_by_trace_id: exit — hit",
            extra={"_fields": {
                "trace_id": trace_id, "reflection_id": ref.reflection_id,
            }},
        )
        return ref

    async def recent_for_owl(
        self, owl_name: str, limit: int = 5,
    ) -> list[Reflection]:
        """Return the N most recent reflections for an owl. Used as a fallback
        when semantic recall isn't wired in classify.py."""
        log.memory.debug(
            "[reflections] recent_for_owl: entry",
            extra={"_fields": {"owl_name": owl_name, "limit": limit}},
        )
        rows = await self._db.fetch_all(
            """SELECT reflection_id, trace_id, owl_name, summary, suggested_strategy,
                      failure_class, quality_score, embedding, embedding_model, created_at
               FROM reflections WHERE owner_id = ? AND owl_name = ?
               ORDER BY created_at DESC LIMIT ?""",
            (self._owner_id, owl_name, limit),
        )
        results = [_row_to_reflection(r) for r in rows]
        log.memory.debug(
            "[reflections] recent_for_owl: exit",
            extra={"_fields": {"owl_name": owl_name, "n": len(results)}},
        )
        return results

    async def semantic_for_owl(
        self,
        owl_name: str,
        query: str,
        embeddings: EmbeddingRegistry,
        *,
        limit: int = 5,
        candidate_cap: int = 200,
    ) -> list[Reflection]:
        """Return reflections for an owl ranked by SEMANTIC closeness to ``query``.

        F-50: the richer sibling of :meth:`recent_for_owl`. Where ``recent_for_owl``
        is pure recency (last-N by ``created_at``), this surfaces the reflections
        whose embedding best matches the *current intent* (``query``), so recall
        is relevant rather than merely recent. Recency is the tie-breaker among
        equally-close candidates.

        Positive-only by construction — reflections are written only for
        successful, high-quality outcomes (see module docstring), so this recall
        path never resurfaces a "this failed / I can't" memory.

        ANN strategy: reflections live in SQLite (not LanceDB), so we score the
        owl's embedded reflections in-process with cosine similarity — the same
        primitive the rest of memory uses (:func:`sqlite_helpers.cosine_similarity`).
        The candidate set is bounded (positive-only + owl-scoped + ``candidate_cap``),
        so the in-process pass stays cheap. Degrades to :meth:`recent_for_owl`
        whenever embedding is unavailable (empty query, embed failure, or no
        embedded candidates) so recall is never empty and never crashes.
        """
        # 1. ENTRY
        log.memory.debug(
            "[reflections] semantic_for_owl: entry",
            extra={"_fields": {
                "owl_name": owl_name, "limit": limit,
                "query_len": len(query), "candidate_cap": candidate_cap,
            }},
        )
        # 2. DECISION — empty query can't be embedded; fall back to recency.
        if not query.strip():
            log.memory.debug(
                "[reflections] semantic_for_owl: empty query — recency fallback",
                extra={"_fields": {"owl_name": owl_name}},
            )
            return await self.recent_for_owl(owl_name, limit=limit)

        # 3. STEP — embed the intent (best-effort; recency fallback on failure).
        try:
            vectors = await embeddings.get().embed([query])
        except Exception as exc:  # B5 — never crash recall on an embed failure
            log.memory.warning(
                "[reflections] semantic_for_owl: embed failed — recency fallback",
                exc_info=exc,
                extra={"_fields": {"owl_name": owl_name}},
            )
            return await self.recent_for_owl(owl_name, limit=limit)
        query_vec = list(vectors[0]) if vectors and vectors[0] else None
        if query_vec is None:
            log.memory.debug(
                "[reflections] semantic_for_owl: empty embedding — recency fallback",
                extra={"_fields": {"owl_name": owl_name}},
            )
            return await self.recent_for_owl(owl_name, limit=limit)

        # 3. STEP — pull the owl's embedded reflections, newest-first so the
        # stable sort below yields recency tie-breaking for free.
        rows = await self._db.fetch_all(
            """SELECT reflection_id, trace_id, owl_name, summary, suggested_strategy,
                      failure_class, quality_score, embedding, embedding_model, created_at
               FROM reflections
               WHERE owner_id = ? AND owl_name = ? AND embedding IS NOT NULL
               ORDER BY created_at DESC LIMIT ?""",
            (self._owner_id, owl_name, candidate_cap),
        )
        # 2. DECISION — no embedded candidates: degrade to pure recency.
        if not rows:
            log.memory.debug(
                "[reflections] semantic_for_owl: no embedded candidates — recency fallback",
                extra={"_fields": {"owl_name": owl_name}},
            )
            return await self.recent_for_owl(owl_name, limit=limit)

        scored: list[tuple[float, Reflection]] = []
        for r in rows:
            ref = _row_to_reflection(r)
            sim = cosine_similarity(query_vec, ref.embedding)
            scored.append((sim if sim is not None else -1.0, ref))
        # Stable sort by similarity desc; rows already came created_at DESC, so
        # equal-similarity ties keep newest-first (recency tie-break).
        scored.sort(key=lambda pair: pair[0], reverse=True)
        results = [ref for _, ref in scored[:limit]]
        # 4. EXIT
        log.memory.debug(
            "[reflections] semantic_for_owl: exit",
            extra={"_fields": {
                "owl_name": owl_name, "n": len(results),
                "candidates": len(rows),
                "top_sim": round(scored[0][0], 4) if scored else None,
            }},
        )
        return results


def _row_to_reflection(row: dict[str, object]) -> Reflection:
    qs = row.get("quality_score")
    emb_raw = row.get("embedding")
    embedding = None
    if isinstance(emb_raw, bytes | bytearray | memoryview):
        try:
            embedding = unpack_embedding(bytes(emb_raw))
        except Exception:  # B5
            log.memory.warning(
                "[reflections] _row_to_reflection: unpack_embedding failed",
                extra={"_fields": {"reflection_id": row.get("reflection_id")}},
            )
            embedding = None
    return Reflection(
        reflection_id=int(str(row["reflection_id"])),
        trace_id=str(row["trace_id"]),
        owl_name=str(row["owl_name"]),
        summary=str(row["summary"]),
        suggested_strategy=str(row.get("suggested_strategy", "")),
        failure_class=str(row["failure_class"]) if row.get("failure_class") else None,
        quality_score=float(str(qs)) if qs is not None else None,
        embedding=embedding,
        embedding_model=str(row["embedding_model"]) if row.get("embedding_model") else None,
        created_at=float(str(row["created_at"])),
    )
