"""DreamWorker helpers — checkpoint model, phase ordering, DB I/O, audit writes.

The :class:`DreamWorkerCheckpoint` model lives here (not in ``dream_worker.py``)
to keep the import graph acyclic (B1) — helpers reference the model and the
handler re-exports it.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict

from stackowl.infra.observability import log
from stackowl.memory.models import MemoryRecord
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
   SET completed_at = ?, phase = 'complete'
 WHERE run_id = ?
"""

_SELECT_COMMITTED_FACTS_SQL = """
SELECT fact_id, content, embedding, embedding_model, committed_at,
       source_type, source_ref, tags
FROM committed_facts
"""

_INSERT_AUDIT_SQL = """
INSERT INTO audit_log (audit_id, event_type, actor, target, timestamp, details)
VALUES (?, ?, ?, ?, ?, ?)
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
    """Mark a run completed_at = now and set phase = 'complete'."""
    now_iso = datetime.now(UTC).isoformat()
    await db.execute(_FINALIZE_RUN_SQL, (now_iso, run_id))
    log.memory.info(
        "[memory] dw_helpers.finalize_run: exit",
        extra={"_fields": {"run_id": run_id, "completed_at": now_iso}},
    )


async def load_committed_for_scan(db: DbPool) -> list[MemoryRecord]:
    """Load every committed fact with its decoded embedding for the detector."""
    log.memory.debug("[memory] dream_worker_helpers.load_committed_for_scan: entry")
    rows = await db.fetch_all(_SELECT_COMMITTED_FACTS_SQL)
    records: list[MemoryRecord] = []
    for row in rows:
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
    now_iso = datetime.now(UTC).isoformat()
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
            await db.execute(
                _INSERT_AUDIT_SQL,
                (
                    str(uuid.uuid4()),
                    _AUDIT_EVENT_TYPE,
                    "dream_worker",
                    report.fact_id_a,
                    now_iso,
                    details,
                ),
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
    "finalize_run",
    "load_committed_for_scan",
    "mark_audit_contradictions",
    "select_resumable_run",
]
