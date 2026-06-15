"""DreamWorker helpers — checkpoint model, phase ordering, DB I/O, audit writes.

The :class:`DreamWorkerCheckpoint` model lives here (not in ``dream_worker.py``)
to keep the import graph acyclic (B1) — helpers reference the model and the
handler re-exports it.
"""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict

from stackowl.infra.observability import log
from stackowl.memory.models import MemoryRecord
from stackowl.memory.sqlite_helpers import pack_embedding as _pack_embedding
from stackowl.memory.sqlite_helpers import unpack_embedding

if TYPE_CHECKING:  # pragma: no cover — typing-only imports
    from stackowl.db.pool import DbPool
    from stackowl.memory.contradiction_detector import ContradictionReport


PhaseName = Literal[
    "contradiction", "promotion", "pruning", "kuzu_sync", "complete"
]


# Phase order is the canonical state machine for the consolidation pass.
PHASE_ORDER: tuple[PhaseName, ...] = (
    "contradiction",
    "promotion",
    "pruning",
    "kuzu_sync",
    "complete",
)

_RESUME_WINDOW_HOURS = 25
_AUDIT_EVENT_TYPE = "memory.contradiction"

# Mirrors FactPromoter._SELECT_ELIGIBLE_SQL EXACTLY (same gate: per-source
# reinforcement, confidence threshold, settle cutoff) but COUNTs rows still
# status='staged' — the OUTCOME signal that eligible memories never moved
# short→long. Kept in lock-step with the promoter query by review.
_COUNT_STUCK_ELIGIBLE_SQL = """
SELECT COUNT(*) AS n
FROM staged_facts
WHERE status = 'staged'
  AND confidence >= ?
  AND (
        (source_type = 'conversation_fact' AND reinforcement_count >= ?)
     OR (source_type != 'conversation_fact' AND reinforcement_count >= ?)
  )
  AND staged_at <= ?
"""

_MARK_FAILED_SQL = """
UPDATE dreamworker_runs
   SET status = 'failed', error = ?, completed_at = ?
 WHERE run_id = ?
"""

_RECORD_STUCK_SQL = (
    "UPDATE dreamworker_runs SET stuck_eligible = ? WHERE run_id = ?"
)


class DreamWorkerCheckpoint(BaseModel):
    """Snapshot of a single DreamWorker pass — persisted to ``dreamworker_runs``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str
    started_at: str
    phase: PhaseName
    facts_processed: int = 0
    facts_promoted: int = 0
    facts_pruned: int = 0
    contradictions_found: int = 0


_SELECT_INCOMPLETE_SQL = """
SELECT run_id, started_at, phase,
       facts_processed, facts_promoted, facts_pruned, contradictions_found
FROM dreamworker_runs
WHERE completed_at IS NULL
ORDER BY started_at DESC
LIMIT 1
"""

_UPDATE_PHASE_SQL = """
UPDATE dreamworker_runs
   SET phase = ?,
       facts_processed = ?,
       facts_promoted = ?,
       facts_pruned = ?,
       contradictions_found = ?
 WHERE run_id = ?
"""

_FINALIZE_RUN_SQL = """
UPDATE dreamworker_runs
   SET completed_at = ?, phase = 'complete', status = 'completed'
 WHERE run_id = ?
"""

_SELECT_COMMITTED_FACTS_SQL = """
SELECT fact_id, content, embedding, embedding_model, committed_at,
       source_type, source_ref, tags, trust
FROM committed_facts
"""

def _parse_iso(value: str) -> datetime:
    """Parse an ISO8601 string into an aware UTC datetime."""
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _is_recent(started_at: str) -> bool:
    """Return ``True`` when ``started_at`` is inside the resume window."""
    try:
        started_dt = _parse_iso(started_at)
    except (ValueError, TypeError) as exc:
        # B5
        log.memory.warning(
            "[memory] dream_worker_helpers: bad started_at — treating as stale",
            exc_info=exc,
            extra={"_fields": {"started_at": started_at}},
        )
        return False
    delta = datetime.now(UTC) - started_dt
    return delta.total_seconds() < _RESUME_WINDOW_HOURS * 3600


async def select_resumable_run(db: DbPool) -> DreamWorkerCheckpoint | None:
    """Return the most-recent incomplete run if it's still within 25h."""
    log.memory.debug("[memory] dw_helpers.select_resumable_run: entry")
    rows = await db.fetch_all(_SELECT_INCOMPLETE_SQL)
    if not rows:
        log.memory.debug("[memory] dw_helpers.select_resumable_run: exit — none")
        return None
    row = rows[0]
    started_at = row["started_at"]
    if not _is_recent(started_at):
        log.memory.info(
            "[memory] dw_helpers.select_resumable_run: incomplete row stale",
            extra={"_fields": {"run_id": row["run_id"], "started_at": started_at}},
        )
        return None
    checkpoint = DreamWorkerCheckpoint(
        run_id=row["run_id"],
        started_at=started_at,
        phase=row["phase"],
        facts_processed=int(row["facts_processed"]),
        facts_promoted=int(row["facts_promoted"]),
        facts_pruned=int(row["facts_pruned"]),
        contradictions_found=int(row["contradictions_found"]),
    )
    log.memory.info(
        "[memory] dw_helpers.select_resumable_run: exit — resuming",
        extra={"_fields": {"run_id": checkpoint.run_id, "phase": checkpoint.phase}},
    )
    return checkpoint


async def advance_phase(
    db: DbPool,
    checkpoint: DreamWorkerCheckpoint,
    next_phase: PhaseName,
) -> DreamWorkerCheckpoint:
    """Persist a phase transition; returns the updated in-memory checkpoint."""
    log.memory.debug(
        "[memory] dw_helpers.advance_phase: entry",
        extra={
            "_fields": {
                "run_id": checkpoint.run_id,
                "from_phase": checkpoint.phase,
                "to_phase": next_phase,
            }
        },
    )
    updated = checkpoint.model_copy(update={"phase": next_phase})
    await db.execute(
        _UPDATE_PHASE_SQL,
        (
            updated.phase,
            updated.facts_processed,
            updated.facts_promoted,
            updated.facts_pruned,
            updated.contradictions_found,
            updated.run_id,
        ),
    )
    log.memory.debug(
        "[memory] dw_helpers.advance_phase: exit",
        extra={"_fields": {"run_id": updated.run_id, "phase": updated.phase}},
    )
    return updated


async def finalize_run(db: DbPool, run_id: str) -> None:
    """Mark a run completed_at = now, phase = 'complete', status = 'completed'."""
    now_iso = datetime.now(UTC).isoformat()
    await db.execute(_FINALIZE_RUN_SQL, (now_iso, run_id))
    log.memory.info(
        "[memory] dw_helpers.finalize_run: exit",
        extra={"_fields": {"run_id": run_id, "completed_at": now_iso}},
    )


async def mark_run_failed(
    db: DbPool, run_id: str, phase: str, error: str
) -> None:
    """Record a terminal failure on a run (status='failed' + error + completed_at).

    Wrapped in its own try/except logged at WARNING so a tracker-write failure
    can never mask the original error that the caller is about to surface (no
    hidden errors — degrade loudly, never silently).
    """
    # 1. ENTRY
    log.memory.debug(
        "[memory] dw_helpers.mark_run_failed: entry",
        extra={"_fields": {"run_id": run_id, "phase": phase}},
    )
    now_iso = datetime.now(UTC).isoformat()
    # Truncate the error so a giant traceback can't bloat the row.
    error_text = f"{phase}: {error}"[:2000]
    try:
        await db.execute(_MARK_FAILED_SQL, (error_text, now_iso, run_id))
    except Exception as exc:  # B5 — tracker write must not mask the real error
        log.memory.warning(
            "[memory] dw_helpers.mark_run_failed: tracker write FAILED",
            exc_info=exc,
            extra={"_fields": {"run_id": run_id, "phase": phase}},
        )
        return
    # 4. EXIT
    log.memory.warning(
        "[memory] dw_helpers.mark_run_failed: run marked failed",
        extra={"_fields": {"run_id": run_id, "phase": phase}},
    )


async def retry_once_promotion(
    promote: Callable[[], Awaitable[int]],
) -> int:
    """Run the promotion call with bounded retry-once.

    On the first exception, log at ERROR and retry exactly once. If the retry
    also raises, re-raise so the caller's failure path records status='failed'.
    Only the promotion phase is retried — checkpoint-resume + the cadence cover
    whole-run recovery.
    """
    try:
        return await promote()
    except Exception as exc:  # B5 — loud, then one bounded retry
        log.memory.error(
            "[memory] dw_helpers.retry_once_promotion: promotion failed — retrying once",
            exc_info=exc,
        )
        return await promote()


async def count_stuck_eligible(
    db: DbPool,
    *,
    confidence_threshold: float,
    conversation_fact_reinforcement_required: int,
    reinforcement_required: int,
    settle_cutoff: str,
) -> int:
    """Count staged facts that SHOULD have promoted but are still status='staged'.

    Mirrors the promoter eligibility gate exactly (per-source reinforcement,
    confidence threshold, settle cutoff) so a non-zero result means eligible
    memories failed to move short→long — the OUTCOME we verify, not merely that
    the promotion phase ran.
    """
    rows = await db.fetch_all(
        _COUNT_STUCK_ELIGIBLE_SQL,
        (
            confidence_threshold,
            conversation_fact_reinforcement_required,
            reinforcement_required,
            settle_cutoff,
        ),
    )
    count = int(rows[0]["n"]) if rows else 0
    log.memory.debug(
        "[memory] dw_helpers.count_stuck_eligible: exit",
        extra={"_fields": {"stuck": count}},
    )
    return count


async def record_stuck_eligible(db: DbPool, run_id: str, count: int) -> None:
    """Persist the stuck-eligible count on the run row (best-effort, loud on fail)."""
    try:
        await db.execute(_RECORD_STUCK_SQL, (count, run_id))
    except Exception as exc:  # B5
        log.memory.warning(
            "[memory] dw_helpers.record_stuck_eligible: write FAILED",
            exc_info=exc,
            extra={"_fields": {"run_id": run_id, "count": count}},
        )


async def get_contradiction_watermark(db: DbPool) -> str | None:
    """Return the last-scanned committed_at high-water mark, or ``None``.

    ``None`` (never scanned) means the next scan covers the whole corpus.
    """
    try:
        rows = await db.fetch_all(
            "SELECT last_contradiction_scan_at AS wm FROM contradiction_scan_state WHERE id = 1"
        )
    except Exception as exc:
        # B5 — a missing watermark table must degrade to a full scan, not crash.
        log.memory.warning(
            "[memory] dw_helpers.get_contradiction_watermark: read failed — full scan",
            exc_info=exc,
        )
        return None
    if not rows:
        return None
    wm = rows[0]["wm"]
    return str(wm) if wm is not None else None


async def get_contradiction_boundary_ids(db: DbPool) -> list[str]:
    """Return the fact_ids already scanned AT the watermark timestamp.

    These are excluded from the next ``committed_at >= watermark`` scan so a
    same-millisecond boundary pair is never re-emitted while a *new* fact landing
    in that same millisecond is still scanned. Empty list = no exclusions.
    """
    try:
        rows = await db.fetch_all(
            "SELECT boundary_fact_ids AS ids FROM contradiction_scan_state WHERE id = 1"
        )
    except Exception as exc:
        # B5 — a missing column/table must degrade to no-exclusions, not crash.
        log.memory.warning(
            "[memory] dw_helpers.get_contradiction_boundary_ids: read failed — none",
            exc_info=exc,
        )
        return []
    if not rows:
        return []
    raw = rows[0]["ids"]
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError) as exc:
        # B5 — corrupt JSON degrades to no-exclusions (scan re-checks the pair).
        log.memory.warning(
            "[memory] dw_helpers.get_contradiction_boundary_ids: bad json — none",
            exc_info=exc,
        )
        return []
    return [str(x) for x in parsed] if isinstance(parsed, list) else []


async def set_contradiction_watermark(
    db: DbPool, watermark: str, boundary_ids: list[str] | None = None
) -> None:
    """Advance the contradiction-scan watermark. Call ONLY after the scan commits.

    ``boundary_ids`` are the fact_ids whose ``committed_at`` equals ``watermark``
    (the boundary timestamp). They are persisted atomically with the watermark so
    the next ``committed_at >= watermark`` scan can exclude them — closing the
    same-millisecond blind spot without re-emitting the boundary pair.
    """
    log.memory.debug(
        "[memory] dw_helpers.set_contradiction_watermark: advancing",
        extra={"_fields": {"watermark": watermark, "boundary_n": len(boundary_ids or [])}},
    )
    ids_json = json.dumps(list(boundary_ids)) if boundary_ids else None
    await db.execute(
        "UPDATE contradiction_scan_state "
        "SET last_contradiction_scan_at = ?, boundary_fact_ids = ? WHERE id = 1",
        (watermark, ids_json),
    )


async def load_committed_for_scan(
    db: DbPool,
    since: str | None = None,
    exclude_ids: list[str] | None = None,
) -> list[MemoryRecord]:
    """Load committed facts with decoded embeddings for the detector.

    ``since`` (the contradiction watermark) bounds the LEFT side of the scan to
    facts committed AT-OR-AFTER it — THIS is the RAM/CPU hotspot fix (only
    new/changed facts are loaded each run). The bound is ``>=`` (not ``>``) so a
    fact stamped in the SAME millisecond as the watermark is never skipped;
    ``exclude_ids`` (the boundary fact_ids already scanned at that millisecond)
    are filtered out so the boundary pair is not re-emitted. ``None`` loads the
    whole corpus (first run / brute-force fallback path).
    """
    exclude = set(exclude_ids or ())
    log.memory.debug(
        "[memory] dream_worker_helpers.load_committed_for_scan: entry",
        extra={"_fields": {"since": since, "exclude_n": len(exclude)}},
    )
    if since is not None:
        rows = await db.fetch_all(
            _SELECT_COMMITTED_FACTS_SQL + " WHERE committed_at >= ?",
            (since,),
        )
    else:
        rows = await db.fetch_all(_SELECT_COMMITTED_FACTS_SQL)
    records: list[MemoryRecord] = []
    for row in rows:
        if exclude and row["fact_id"] in exclude:
            # Already scanned at the boundary millisecond — skip to avoid
            # re-emitting an unchanged pair (the same-ms blind-spot fix).
            continue
        try:
            embedding = unpack_embedding(row["embedding"]) or []
            tags_raw = row["tags"] or "[]"
            tags = json.loads(tags_raw) if isinstance(tags_raw, str) else []
            records.append(
                MemoryRecord(
                    fact_id=row["fact_id"],
                    content=row["content"],
                    embedding=embedding,
                    embedding_model=row["embedding_model"] or "",
                    committed_at=_parse_iso(row["committed_at"]),
                    source_type=row["source_type"],
                    source_ref=row["source_ref"],
                    tags=list(tags) if isinstance(tags, list) else [],
                    trust=row["trust"],
                )
            )
        except Exception as exc:
            # B5 — never let one bad row poison the scan
            log.memory.warning(
                "[memory] dream_worker_helpers.load_committed_for_scan: bad row skipped",
                exc_info=exc,
                extra={"_fields": {"fact_id": row.get("fact_id", "?")}},
            )
    log.memory.debug(
        "[memory] dream_worker_helpers.load_committed_for_scan: exit",
        extra={"_fields": {"row_count": len(rows), "kept": len(records)}},
    )
    return records


async def reembed_committed_facts(
    db: DbPool,
    lancedb: object,
    *,
    embed: Callable[[list[str]], Awaitable[list[list[float]]]],
    active_model: str,
    active_dim: int,
    batch_cap: int | None = None,
) -> int:
    """F066/F062 cure — rebuild the LanceDB corpus at the active model/dim.

    Sources every committed fact from the SQLite ``committed_facts`` SoT (never
    the about-to-drop LanceDB table), re-embeds the content through ``embed``,
    rebuilds the table at ``active_dim`` (drop+recreate via the reindex
    target-dim path), writes the corpus-identity sidecar, and updates each
    fact's ``embedding`` + ``embedding_model`` in SQLite so the SoT and the
    vector store agree. Returns the number of facts re-embedded.

    ``batch_cap`` is available for host-scaling, but the dream-worker caller
    intentionally rebuilds in ONE pass (cap=None): the sidecar is stamped to the
    new identity only after the table is fully populated, so the F062 recall gate
    matches ONLY a complete corpus. A capped (partial) rebuild would have to stamp
    the sidecar while incomplete — recall would then serve a partial corpus as
    "matched/confirmed". Until the (rare, model-swap-triggered) rebuild finishes,
    recall stays honest on FTS via the F062 gate (corpus still mismatched).
    Build-new-then-swap: on any embed failure the old table is left intact and
    recall stays on FTS.
    """
    log.memory.info(
        "[memory] dw_helpers.reembed_committed_facts: entry",
        extra={"_fields": {"active_model": active_model, "active_dim": active_dim, "cap": batch_cap}},
    )
    rows = await db.fetch_all(_SELECT_COMMITTED_FACTS_SQL)
    if batch_cap is not None and batch_cap > 0:
        rows = rows[:batch_cap]
    if not rows:
        log.memory.info("[memory] dw_helpers.reembed_committed_facts: exit — no committed facts")
        return 0

    fact_ids = [row["fact_id"] for row in rows]
    contents = [row["content"] for row in rows]
    try:
        vectors = await embed(contents)
    except Exception as exc:
        # B5 — re-embedding failed; leave the old corpus intact, stay on FTS.
        log.memory.error(
            "[memory] dw_helpers.reembed_committed_facts: embed failed — corpus unchanged",
            exc_info=exc,
            extra={"_fields": {"count": len(contents)}},
        )
        return 0
    if len(vectors) != len(rows) or any(len(v) != active_dim for v in vectors):
        log.memory.error(
            "[memory] dw_helpers.reembed_committed_facts: embed shape mismatch — corpus unchanged",
            extra={
                "_fields": {
                    "expected_n": len(rows),
                    "got_n": len(vectors),
                    "active_dim": active_dim,
                }
            },
        )
        return 0

    records: list[tuple[str, list[float], dict[str, object]]] = [
        (
            row["fact_id"],
            vectors[i],
            {
                "source_type": row["source_type"],
                "source_ref": row["source_ref"],
                "content": row["content"],
                "trust": row["trust"],
                "embedding_model": active_model,
            },
        )
        for i, row in enumerate(rows)
    ]
    # Rebuild the LanceDB table at the new dim (drop+recreate+fill), then stamp
    # the sidecar so the F062 recall gate now MATCHES and semantic resumes.
    written = await lancedb.reindex(records, target_dim=active_dim)  # type: ignore[attr-defined]
    await lancedb.set_corpus_identity(active_model, active_dim)  # type: ignore[attr-defined]

    # Keep the SQLite SoT consistent: update each fact's embedding + model so a
    # later contradiction scan / FTS row carries the new identity.
    for i, fact_id in enumerate(fact_ids):
        blob = _pack_embedding(vectors[i])
        await db.execute(
            "UPDATE committed_facts SET embedding = ?, embedding_model = ? WHERE fact_id = ?",
            (blob, active_model, fact_id),
        )
    log.memory.info(
        "[memory] dw_helpers.reembed_committed_facts: exit",
        extra={"_fields": {"written": written, "active_model": active_model}},
    )
    return int(written)


async def count_committed_with_vectors(db: DbPool) -> int:
    """Count committed facts that carry a non-empty embedding blob.

    Used by the drift-cure phase to decide whether there is anything to
    re-embed: a corpus with zero vectored facts has nothing to rebuild. The
    ``committed_facts`` access lives here (with the rest of the dual-bridge SQL)
    rather than inline in the handler so the owner-scope register stays in one
    allowlisted place.
    """
    rows = await db.fetch_all(
        "SELECT COUNT(*) AS n FROM committed_facts "
        "WHERE embedding IS NOT NULL AND LENGTH(embedding) > 0"
    )
    count = int(rows[0]["n"]) if rows else 0
    log.memory.debug(
        "[memory] dw_helpers.count_committed_with_vectors: exit",
        extra={"_fields": {"vectored": count}},
    )
    return count


async def count_committed_facts(db: DbPool) -> int:
    """Total committed-fact rows — sizes the contradiction-scan strategy.

    Below the ANN threshold the brute-force O(n^2) scan is used; at/above it the
    incremental watermark scan kicks in. The ``committed_facts`` read lives here
    with the rest of the dual-bridge SQL.
    """
    rows = await db.fetch_all("SELECT COUNT(*) AS n FROM committed_facts")
    count = int(rows[0]["n"]) if rows else 0
    log.memory.debug(
        "[memory] dw_helpers.count_committed_facts: exit",
        extra={"_fields": {"total": count}},
    )
    return count


async def mark_audit_contradictions(
    db: DbPool, reports: list[ContradictionReport]
) -> None:
    """Best-effort append of each report into ``audit_log``.

    Audit writes are non-fatal: a missing table or write failure must never
    cause the consolidation pass to fail. Every failure is logged at WARNING.
    """
    if not reports:
        return
    log.memory.debug(
        "[memory] dream_worker_helpers.mark_audit_contradictions: entry",
        extra={"_fields": {"report_count": len(reports)}},
    )
    for report in reports:
        try:
            details = json.dumps(
                {
                    "fact_id_a": report.fact_id_a,
                    "fact_id_b": report.fact_id_b,
                    "explanation": report.explanation,
                    "confidence": report.confidence,
                }
            )
            # Chain through the canonical audit chokepoint (C7 / F130b) so this
            # contradiction row carries a v2 integrity_hash and does NOT void
            # verify_chain (previously wrote integrity_hash='').
            from stackowl.audit.logger import chain_append_via_pool

            await chain_append_via_pool(
                db,
                _AUDIT_EVENT_TYPE,
                "dream_worker",
                report.fact_id_a,
                time.time(),
                details,
            )
        except Exception as exc:
            # B5 — audit failure must not abort the consolidation pass
            log.memory.warning(
                "[memory] dream_worker_helpers.mark_audit_contradictions: write failed",
                exc_info=exc,
                extra={
                    "_fields": {
                        "fact_id_a": report.fact_id_a,
                        "fact_id_b": report.fact_id_b,
                    }
                },
            )
    log.memory.debug(
        "[memory] dream_worker_helpers.mark_audit_contradictions: exit",
        extra={"_fields": {"written": len(reports)}},
    )


__all__: list[str] = [
    "PHASE_ORDER",
    "DreamWorkerCheckpoint",
    "PhaseName",
    "advance_phase",
    "count_committed_facts",
    "count_committed_with_vectors",
    "count_stuck_eligible",
    "finalize_run",
    "get_contradiction_boundary_ids",
    "get_contradiction_watermark",
    "load_committed_for_scan",
    "mark_audit_contradictions",
    "mark_run_failed",
    "record_stuck_eligible",
    "reembed_committed_facts",
    "retry_once_promotion",
    "select_resumable_run",
    "set_contradiction_watermark",
]
