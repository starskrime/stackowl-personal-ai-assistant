"""MemoryPruner — deletes stale low-confidence committed facts."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from stackowl.infra.observability import log

if TYPE_CHECKING:  # pragma: no cover — typing-only imports
    from stackowl.db.pool import DbPool


_SELECT_PRUNE_CANDIDATES_SQL = """
SELECT cf.fact_id
FROM committed_facts cf
JOIN staged_facts sf ON sf.fact_id = cf.fact_id
WHERE sf.confidence < ?
  AND cf.committed_at < datetime('now', '-' || ? || ' days')
  AND sf.reinforcement_count = 0
"""

_SELECT_ROWID_SQL = "SELECT rowid AS rid FROM committed_facts WHERE fact_id = ?"
_DELETE_COMMITTED_SQL = "DELETE FROM committed_facts WHERE fact_id = ?"
_DELETE_FTS_SQL = "DELETE FROM committed_facts_fts WHERE rowid = ?"
_DELETE_STAGED_SQL = "DELETE FROM staged_facts WHERE fact_id = ?"


class PruneReport(BaseModel):
    """Outcome of a single :meth:`MemoryPruner.prune` call."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    pruned_count: int
    kept_count: int
    errors: list[str] = Field(default_factory=list)


class MemoryPruner:
    """Prunes committed facts that are low-confidence, stale, and unreinforced."""

    def __init__(
        self,
        db: DbPool,
        prune_after_days: int = 90,
        confidence_threshold: float = 0.4,
    ) -> None:
        # 1. ENTRY
        log.memory.debug(
            "[memory] memory_pruner.init: entry",
            extra={
                "_fields": {
                    "prune_after_days": prune_after_days,
                    "confidence_threshold": confidence_threshold,
                }
            },
        )
        self._db = db
        self._prune_after_days = prune_after_days
        self._confidence_threshold = confidence_threshold
        # 4. EXIT
        log.memory.debug("[memory] memory_pruner.init: exit")

    async def prune(self) -> PruneReport:
        """Delete committed facts matching the prune criteria."""
        # 1. ENTRY
        log.memory.debug("[memory] memory_pruner.prune: entry")

        # 3. STEP — count remaining committed first (for kept_count)
        before_rows = await self._db.fetch_all(
            "SELECT COUNT(*) AS cnt FROM committed_facts"
        )
        before_count = int(before_rows[0]["cnt"]) if before_rows else 0

        # 3. STEP — select candidates
        candidate_rows = await self._db.fetch_all(
            _SELECT_PRUNE_CANDIDATES_SQL,
            (self._confidence_threshold, self._prune_after_days),
        )
        log.memory.debug(
            "[memory] memory_pruner.prune: candidates fetched",
            extra={"_fields": {"candidate_count": len(candidate_rows)}},
        )

        pruned = 0
        errors: list[str] = []
        for row in candidate_rows:
            fact_id = row["fact_id"]
            try:
                await self._delete_one(fact_id)
                pruned += 1
            except Exception as exc:
                # B5
                msg = f"prune {fact_id}: {exc}"
                log.memory.warning(
                    "[memory] memory_pruner.prune: delete failed",
                    exc_info=exc,
                    extra={"_fields": {"fact_id": fact_id}},
                )
                errors.append(msg)

        kept = max(0, before_count - pruned)
        report = PruneReport(pruned_count=pruned, kept_count=kept, errors=errors)
        # 4. EXIT
        log.memory.info(
            "[memory] memory_pruner.prune: exit",
            extra={
                "_fields": {
                    "pruned_count": pruned,
                    "kept_count": kept,
                    "error_count": len(errors),
                }
            },
        )
        return report

    # ------------------------------------------------------------------ helpers

    async def _delete_one(self, fact_id: str) -> None:
        rowid_rows = await self._db.fetch_all(_SELECT_ROWID_SQL, (fact_id,))
        for r in rowid_rows:
            await self._db.execute(_DELETE_FTS_SQL, (r["rid"],))
        await self._db.execute(_DELETE_COMMITTED_SQL, (fact_id,))
        await self._db.execute(_DELETE_STAGED_SQL, (fact_id,))
        log.memory.info(
            "[memory] memory_pruner: pruned",
            extra={"_fields": {"fact_id": fact_id}},
        )
