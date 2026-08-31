"""OrphanReconciliationHandler — identity rows whose owner no longer exists.

Bakir, 2026-08-31, choosing between a one-off migration and a standing sweep:
"A self-healing sweep, not a one-off — a scheduled reconciler that deletes rows
whose owner no longer exists, running until it finds nothing. Fixes today's
damage AND anything a future gap creates." Daily.

UNCAPPED, BY HIS EXPLICIT DECISION, and I said once that it was the
highest-variance choice on the board. What makes it acceptable is the other half
he chose: "snapshot the deleted rows before deleting." That shipped FIRST,
deliberately — an uncapped deleting sweep with no snapshot is the 2026-08-30
purge with a timer on it.

MEASURED ON THE LIVE DATABASE the day this was written:
    owl_dna with no owls row          6
    owl_dna_authored with no owls row 11
    dna_checkpoints with no owls row  1
    skill_ownership whose skill is gone 110

WHAT IT DELIBERATELY DOES NOT TOUCH. ``skills_fts`` holds 147 rows for skills
that no longer exist, but it is an INDEX, not identity — the repair for a stale
index is a RESYNC, and deleting from an FTS table row by row is how FTS indexes
get corrupted. Different mechanism, different item.

THE ONE GUARD, AND IT IS NOT A CAP. If the OWNER table is empty, every dependent
row looks orphaned and an uncapped sweep would delete all of them. Bakir's own
rule: "an empty table is a QUESTION, not an answer." He rejected a cap on VOLUME;
this refuses an obviously broken PREMISE, which is a different thing — and it is
the single failure mode that turns "no cap" into catastrophe.

OWLS ARE DELETED THROUGH ``OwlStore.delete``, never with SQL of its own, so the
identity cascade and the deletion record come for free and there is no second
copy of "what it means to remove an owl".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from stackowl.audit.deletions import record_deleted_rows
from stackowl.infra.observability import log
from stackowl.scheduler.base import JobHandler
from stackowl.scheduler.job import Job, JobResult

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.db.pool import DbPool

_ACTOR = "OrphanReconciliationHandler"


@dataclass(frozen=True)
class SweepResult:
    """What one pass did. Returned rather than logged only, so a caller — and a
    test — can assert on it instead of grepping."""

    deleted: int = 0
    refused: bool = False
    errors: int = 0


class OrphanReconciliationHandler(JobHandler):
    """Delete identity rows whose owner is gone. Records every row first."""

    @property
    def handler_name(self) -> str:
        return "orphan_reconciliation"

    #: (dependent table, its key column, the OWNER table it must exist in).
    #: One list, so a table added to an identity cascade is added here too rather
    #: than being silently left behind — which is how the shadows accumulated.
    _RULES: tuple[tuple[str, str, str], ...] = (
        ("owl_dna", "owl_name", "owls"),
        ("owl_dna_authored", "owl_name", "owls"),
        ("dna_checkpoints", "owl_name", "owls"),
        ("skill_ownership", "skill_name", "skills"),
        ("skill_ownership", "owl_name", "owls"),
    )

    #: The column each owner table identifies itself by.
    _OWNER_KEY: dict[str, str] = {"owls": "name", "skills": "name"}

    def __init__(self, db: DbPool) -> None:
        self._db = db

    async def _owner_is_populated(self, owner: str) -> bool:
        rows = await self._db.fetch_all(f"SELECT COUNT(*) AS c FROM {owner}")  # noqa: S608
        return bool(rows and int(rows[0]["c"]) > 0)

    async def sweep(self) -> SweepResult:
        """One pass. Never raises: a sweep that can crash the scheduler is worse
        than a sweep that skips a table and says so."""
        # 1. ENTRY
        log.scheduler.info(
            "[scheduler] orphan_reconciliation.sweep: entry",
            extra={"_fields": {"rules": len(self._RULES)}},
        )
        deleted = 0
        errors = 0
        refused = False
        checked_owners: dict[str, bool] = {}

        for table, column, owner in self._RULES:
            # 2. DECISION — the empty-owner guard, once per owner table.
            if owner not in checked_owners:
                try:
                    checked_owners[owner] = await self._owner_is_populated(owner)
                except Exception as exc:
                    log.scheduler.warning(
                        "[scheduler] orphan_reconciliation: could not read the owner "
                        "table — skipping everything that depends on it",
                        exc_info=exc, extra={"_fields": {"owner": owner}},
                    )
                    checked_owners[owner] = False
                    errors += 1
            if not checked_owners[owner]:
                refused = True
                log.scheduler.warning(
                    "[scheduler] orphan_reconciliation: REFUSING to sweep — the owner "
                    "table is empty, which is a question rather than permission to "
                    "delete every dependent row",
                    extra={"_fields": {"table": table, "owner": owner}},
                )
                continue

            owner_key = self._OWNER_KEY.get(owner)
            if owner_key is None:
                # A rule naming an owner nobody declared a key for is a wiring
                # mistake, not a reason to delete anything. Loud, and skipped.
                log.scheduler.warning(
                    "[scheduler] orphan_reconciliation: no key declared for an owner "
                    "table — skipping this rule rather than guessing",
                    extra={"_fields": {"table": table, "owner": owner}},
                )
                errors += 1
                continue
            try:
                orphans = await self._db.fetch_all(
                    f"SELECT * FROM {table} WHERE {column} NOT IN "  # noqa: S608
                    f"(SELECT {owner_key} FROM {owner})",
                )
            except Exception as exc:
                # One unreadable table must not abandon the rest of the sweep.
                log.scheduler.warning(
                    "[scheduler] orphan_reconciliation: could not read a table — "
                    "continuing with the others",
                    exc_info=exc, extra={"_fields": {"table": table}},
                )
                errors += 1
                continue
            if not orphans:
                continue

            rows = [dict(r) for r in orphans]
            reason = f"{table}.{column} has no matching {owner}.{owner_key}"
            # 3. STEP — RECORD FIRST. This is the whole reason an uncapped sweep
            # is acceptable, so it happens before anything is removed.
            await record_deleted_rows(
                self._db, table=table, rows=rows, reason=reason, actor=_ACTOR,
            )
            try:
                await self._db.execute(
                    f"DELETE FROM {table} WHERE {column} NOT IN "  # noqa: S608
                    f"(SELECT {owner_key} FROM {owner})",
                )
            except Exception as exc:
                log.scheduler.warning(
                    "[scheduler] orphan_reconciliation: recorded but could not delete",
                    exc_info=exc, extra={"_fields": {"table": table}},
                )
                errors += 1
                continue
            deleted += len(rows)
            log.scheduler.info(
                "[scheduler] orphan_reconciliation: removed orphans",
                extra={"_fields": {"table": table, "column": column,
                                   "owner": owner, "rows": len(rows),
                                   "reason": reason}},
            )

        # 4. EXIT
        log.scheduler.info(
            "[scheduler] orphan_reconciliation.sweep: exit",
            extra={"_fields": {"deleted": deleted, "refused": refused,
                               "errors": errors}},
        )
        return SweepResult(deleted=deleted, refused=refused, errors=errors)

    async def execute(self, job: Job) -> JobResult:
        """Scheduler entry point. Never fails the tick — a sweep that can crash
        the scheduler would take every other job down with it."""
        import time as _time

        t0 = _time.monotonic()
        result = await self.sweep()
        return JobResult(
            job_id=job.job_id,
            effect_class="state_change",
            success=True,
            output=(
                f"orphan_reconciliation: deleted={result.deleted} "
                f"refused={result.refused} errors={result.errors}"
            ),
            error=None,
            duration_ms=(_time.monotonic() - t0) * 1000,
        )
