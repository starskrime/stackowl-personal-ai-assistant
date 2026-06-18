"""MemoryBudgetEnforcer — JobHandler that keeps memory usage under the ceiling.

When the committed_facts table grows above ``settings.memory.per_user_ceiling_bytes``
the handler prunes the oldest, lowest-confidence facts (paired through
``staged_facts`` on ``fact_id``) until total ``length(content)`` drops back
under the ceiling.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, ClassVar

from stackowl.infra.observability import log
from stackowl.scheduler.base import JobHandler
from stackowl.scheduler.job import Job, JobResult

if TYPE_CHECKING:  # pragma: no cover — typing-only imports
    from stackowl.config.settings import Settings
    from stackowl.db.pool import DbPool


_SUM_SQL = "SELECT COALESCE(SUM(length(content)), 0) AS s FROM committed_facts"

# Oldest first, lowest-confidence first — pairs through staged_facts.
# Facts that have no row in staged_facts are treated as confidence 0 so they
# rank for pruning ahead of anything still tracked there.
_CANDIDATES_SQL = """
SELECT cf.fact_id, length(cf.content) AS sz,
       COALESCE(sf.confidence, 0.0) AS conf,
       cf.committed_at
FROM committed_facts cf
LEFT JOIN staged_facts sf ON sf.fact_id = cf.fact_id
ORDER BY conf ASC, cf.committed_at ASC
"""

_SELECT_ROWID_SQL = "SELECT rowid AS rid FROM committed_facts WHERE fact_id = ?"
_DELETE_COMMITTED_SQL = "DELETE FROM committed_facts WHERE fact_id = ?"
_DELETE_FTS_SQL = "DELETE FROM committed_facts_fts WHERE rowid = ?"
_DELETE_STAGED_SQL = "DELETE FROM staged_facts WHERE fact_id = ?"


class MemoryBudgetEnforcer(JobHandler):
    """Periodic job that enforces per-user memory storage ceiling."""

    _handler_name: ClassVar[str] = "memory_budget"

    def __init__(self, db: DbPool, settings: Settings) -> None:
        # 1. ENTRY
        log.memory.debug(
            "[memory] budget_enforcer.init: entry",
            extra={
                "_fields": {
                    "ceiling_bytes": settings.memory.per_user_ceiling_bytes,
                }
            },
        )
        self._db = db
        self._settings = settings
        # 4. EXIT
        log.memory.debug("[memory] budget_enforcer.init: exit")

    @property
    def handler_name(self) -> str:
        return self._handler_name

    async def execute(self, job: Job) -> JobResult:
        """Run a single enforcement pass."""
        # 1. ENTRY
        log.memory.info(
            "[memory] budget_enforcer.execute: entry",
            extra={"_fields": {"job_id": job.job_id}},
        )
        t0 = time.monotonic()
        ceiling = self._settings.memory.per_user_ceiling_bytes
        try:
            usage = await self._current_usage()
        except Exception as exc:
            # B5
            duration_ms = (time.monotonic() - t0) * 1000.0
            log.memory.error(
                "[memory] budget_enforcer.execute: usage probe failed",
                exc_info=exc,
                extra={"_fields": {"job_id": job.job_id}},
            )
            return JobResult(
                job_id=job.job_id,
                success=False,
                output=None,
                error=f"usage probe failed: {exc}",
                duration_ms=duration_ms,
            )
        # 2. DECISION — already under budget?
        if usage <= ceiling:
            duration_ms = (time.monotonic() - t0) * 1000.0
            log.memory.info(
                "[memory] budget_enforcer.execute: exit — under budget",
                extra={
                    "_fields": {
                        "job_id": job.job_id,
                        "usage_bytes": usage,
                        "ceiling_bytes": ceiling,
                        "pruned_count": 0,
                    }
                },
            )
            return JobResult(
                job_id=job.job_id,
                success=True,
                output=f"under_budget pruned=0 usage={usage} ceiling={ceiling}",
                error=None,
                duration_ms=duration_ms,
            )
        # 3. STEP — prune oldest low-confidence facts
        pruned = await self._prune_until_under(ceiling, usage)
        duration_ms = (time.monotonic() - t0) * 1000.0
        # 4. EXIT
        log.memory.info(
            "[memory] budget_enforcer.execute: exit — pruned",
            extra={
                "_fields": {
                    "job_id": job.job_id,
                    "pruned_count": pruned,
                    "duration_ms": duration_ms,
                }
            },
        )
        return JobResult(
            job_id=job.job_id,
            success=True,
            output=f"pruned={pruned} ceiling={ceiling}",
            error=None,
            duration_ms=duration_ms,
        )

    # ------------------------------------------------------------------ helpers

    async def _current_usage(self) -> int:
        rows = await self._db.fetch_all(_SUM_SQL)
        return int(rows[0]["s"]) if rows else 0

    async def _prune_until_under(self, ceiling: int, current_usage: int) -> int:
        rows = await self._db.fetch_all(_CANDIDATES_SQL)
        pruned = 0
        usage = current_usage
        for row in rows:
            if usage <= ceiling:
                break
            fact_id = row["fact_id"]
            sz = int(row["sz"])
            try:
                await self._delete_one(fact_id)
            except Exception as exc:
                # B5 — continue on individual failure
                log.memory.warning(
                    "[memory] budget_enforcer._prune_until_under: delete failed",
                    exc_info=exc,
                    extra={"_fields": {"fact_id": fact_id}},
                )
                continue
            pruned += 1
            usage -= sz
        return pruned

    async def _delete_one(self, fact_id: str) -> None:
        rowid_rows = await self._db.fetch_all(_SELECT_ROWID_SQL, (fact_id,))
        for r in rowid_rows:
            await self._db.execute(_DELETE_FTS_SQL, (r["rid"],))
        await self._db.execute(_DELETE_COMMITTED_SQL, (fact_id,))
        await self._db.execute(_DELETE_STAGED_SQL, (fact_id,))
        log.memory.info(
            "[memory] budget_enforcer: pruned fact",
            extra={"_fields": {"fact_id": fact_id}},
        )
