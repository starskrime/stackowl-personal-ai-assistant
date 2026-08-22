"""DbReclaimHandler — give the DATABASE FILE a decay leg, not just its rows.

BAKIR, 2026-08-22, after the platform ground to a halt: "Why system itself
doesnot have capability to vacuum database". It did not, and that was the whole
defect.

WHAT WENT WRONG, MEASURED. This platform deletes constantly and deliberately:
``knowledge_prune``, ``downloads_janitor``, ``browser_cache_eviction``, skill
decay (migration 0100), the 107k-fact purge of D08.2, task pruning. Every one of
those frees SQLite PAGES — and SQLite never returns a freed page to the operating
system unless something asks it to. ``auto_vacuum`` was ``NONE``, so nothing ever
did.

    file          922 MB
    live data     279 MB   (dbstat, summed over every table and index)
    FREE pages    643 MB   = 70% of the file
    auto_vacuum   NONE

Seventy percent of the database was reclaimable space that no code path could
reclaim. This is the "no decay" shape this programme keeps finding, one level
below where it usually looks: the ROWS decayed correctly and the FILE never did.

WHY IT BECAME AN OUTAGE RATHER THAN AN EYESORE. The host root filesystem was at
99% (706 MB free on 56 GB), so those 643 wasted megabytes were most of the
remaining headroom. SQLite could not reliably extend the file or its WAL, and the
symptom the operator actually saw was not "disk full" — it was::

    [db] pool.mark_dead: OperationalError: database is locked
    [loop] tick failed — the loop continues
    [telegram] adapter.liveness_heartbeat: crashed — receive-liveness signal lost

The task loop failing every tick, and one Telegram send taking three minutes.
"database is locked" per day ran 0, 13, 3, 20, 51 across 2026-08-17..21 — a
straight climb that tracked the file's growth, which is exactly what a defect
with no decay leg looks like from the outside.

WHAT THIS HANDLER DOES. With ``auto_vacuum=INCREMENTAL`` the freed pages land on
a freelist, and ``PRAGMA incremental_vacuum(N)`` hands N of them back to the OS.
That is a BOUNDED, INTERRUPTIBLE operation — unlike a full ``VACUUM``, which
rewrites the entire database under an exclusive lock and is why this could never
have been a background job before. Running a bounded chunk on a schedule means
the platform gives space back continuously and never needs the outage again.

IT REPORTS WHEN IT CANNOT KEEP UP, which is the half that makes it self-healing
rather than merely automatic. If the free ratio stays above ``_ALERT_FREE_RATIO``
after a pass, the handler logs a WARNING naming the ratio — because a reclaimer
that silently falls behind looks exactly like one that is working, and that is
the failure mode the original ``auto_vacuum=NONE`` had for months.

ONE THING IT CANNOT DO, stated so nobody is surprised: switching a database from
``auto_vacuum=NONE`` to ``INCREMENTAL`` requires one full ``VACUUM`` to take
effect. A database created before this shipped reclaims NOTHING until that
happens once. ``pool.py`` sets the pragma so every NEW database is born correct;
the live one was converted by hand on 2026-08-22 (922 MB -> 273 MB in 35s).
:func:`needs_one_time_vacuum` exists so the condition is detectable rather than
folklore.

Mirrors ``downloads_janitor`` exactly: same 4-point logging, same ``register_*``
factory, same never-raise contract. A maintenance sweep that can take the
platform down is worse than the mess it cleans.
"""

from __future__ import annotations

import time

from stackowl.db.pool import DbPool
from stackowl.infra.observability import log
from stackowl.scheduler.base import HandlerRegistry, JobHandler
from stackowl.scheduler.job import Job, JobResult

#: Pages handed back per pass. 4 KiB pages, so ~8 MB — small enough that the
#: sweep never becomes the writer everything else is waiting behind, which is the
#: mistake that would recreate the lock contention this exists to end.
_DEFAULT_MAX_PAGES = 2000

#: Free-page ratio above which the handler stops being quiet. Chosen from the
#: measured incident: the file sat at 70% free. A healthy database churns, so a
#: small freelist is normal and alerting on any free page at all would be the
#: cries-wolf shape this codebase already pays for elsewhere.
_ALERT_FREE_RATIO = 0.25


async def _page_stats(pool: DbPool) -> tuple[int, int, int]:
    """``(page_size, page_count, freelist_count)``. Never raises."""
    try:
        size = await pool.fetch_all("PRAGMA page_size", ())
        count = await pool.fetch_all("PRAGMA page_count", ())
        free = await pool.fetch_all("PRAGMA freelist_count", ())
        return (
            int(next(iter(size[0].values()))),
            int(next(iter(count[0].values()))),
            int(next(iter(free[0].values()))),
        )
    except Exception as exc:
        # Measurement must never become an outage — the same rule the cache audit
        # follows. An unreadable pragma means we skip a pass, not that we crash
        # the scheduler.
        log.scheduler.warning(
            "[scheduler] db_reclaim: could not read page stats — skipping this pass",
            exc_info=exc,
        )
        return (0, 0, 0)


async def needs_one_time_vacuum(pool: DbPool) -> bool:
    """True when this database still has ``auto_vacuum=NONE``.

    Such a database CANNOT reclaim anything incrementally, no matter how often
    this handler runs — SQLite requires one full VACUUM to convert it. Exposed as
    a function so the condition is checkable in a test and visible in a log line,
    rather than living only in a comment somebody has to find.
    """
    try:
        rows = await pool.fetch_all("PRAGMA auto_vacuum", ())
        return int(next(iter(rows[0].values()))) == 0
    except Exception as exc:
        log.scheduler.warning(
            "[scheduler] db_reclaim: could not read auto_vacuum", exc_info=exc
        )
        return False


class DbReclaimHandler(JobHandler):
    """Hand freed SQLite pages back to the operating system, a chunk at a time.

    Optional job ``params``: ``{"max_pages": 2000}``.
    """

    def __init__(self, pool: DbPool) -> None:
        self._pool = pool

    @property
    def handler_name(self) -> str:
        return "db_reclaim"

    async def execute(self, job: Job) -> JobResult:
        # 1. ENTRY
        t0 = time.monotonic()
        max_pages = int(job.params.get("max_pages", _DEFAULT_MAX_PAGES))
        page_size, pages_before, free_before = await _page_stats(self._pool)
        log.scheduler.info(
            "[scheduler] db_reclaim.execute: entry",
            extra={"_fields": {
                "job_id": job.job_id, "max_pages": max_pages,
                "file_mb": round(page_size * pages_before / 1e6, 1),
                "free_mb": round(page_size * free_before / 1e6, 1),
            }},
        )

        # 2. DECISION — a database still in auto_vacuum=NONE cannot reclaim at
        # all. Say so LOUDLY rather than running a no-op forever and reporting
        # success, which is precisely how the original defect stayed invisible.
        if await needs_one_time_vacuum(self._pool):
            duration_ms = (time.monotonic() - t0) * 1000
            log.scheduler.warning(
                "[scheduler] db_reclaim: auto_vacuum is NONE — this database "
                "cannot reclaim space incrementally and needs ONE offline VACUUM "
                "to convert. Nothing was reclaimed.",
                extra={"_fields": {
                    "job_id": job.job_id,
                    "free_mb": round(page_size * free_before / 1e6, 1),
                }},
            )
            return JobResult(
                job_id=job.job_id, effect_class="state_change", success=True,
                output="skipped: auto_vacuum=NONE, needs a one-time VACUUM",
                error=None, duration_ms=duration_ms,
                metadata={"reclaimed_pages": 0, "needs_vacuum": True},
            )

        # 3. STEP — bounded reclaim.
        try:
            await self._pool.execute(f"PRAGMA incremental_vacuum({max_pages})", ())
        except Exception as exc:
            duration_ms = (time.monotonic() - t0) * 1000
            log.scheduler.warning(
                "[scheduler] db_reclaim: incremental_vacuum failed — space not "
                "reclaimed this pass",
                exc_info=exc, extra={"_fields": {"job_id": job.job_id}},
            )
            return JobResult(
                job_id=job.job_id, effect_class="state_change", success=False,
                output="", error=f"incremental_vacuum failed: {exc}",
                duration_ms=duration_ms, metadata={"reclaimed_pages": 0},
            )

        page_size, pages_after, free_after = await _page_stats(self._pool)
        reclaimed = max(0, pages_before - pages_after)
        free_ratio = (free_after / pages_after) if pages_after else 0.0
        duration_ms = (time.monotonic() - t0) * 1000

        # A reclaimer that silently falls behind looks exactly like one that
        # works. This is the line that would have caught the original defect.
        if free_ratio > _ALERT_FREE_RATIO:
            log.scheduler.warning(
                "[scheduler] db_reclaim: the database is still mostly free space "
                "after a pass — reclaim is not keeping up with churn",
                extra={"_fields": {
                    "job_id": job.job_id,
                    "free_ratio": round(free_ratio, 3),
                    "free_mb": round(page_size * free_after / 1e6, 1),
                    "threshold": _ALERT_FREE_RATIO,
                }},
            )

        # 4. EXIT
        log.scheduler.info(
            "[scheduler] db_reclaim.execute: exit",
            extra={"_fields": {
                "job_id": job.job_id,
                "reclaimed_pages": reclaimed,
                "reclaimed_mb": round(page_size * reclaimed / 1e6, 1),
                "file_mb": round(page_size * pages_after / 1e6, 1),
                "free_ratio": round(free_ratio, 3),
                "duration_ms": duration_ms,
            }},
        )
        return JobResult(
            job_id=job.job_id, effect_class="state_change", success=True,
            output=f"reclaimed_pages={reclaimed} file_mb={page_size * pages_after / 1e6:.1f}",
            error=None, duration_ms=duration_ms,
            metadata={
                "reclaimed_pages": reclaimed,
                "reclaimed_bytes": page_size * reclaimed,
                "free_ratio": free_ratio,
            },
        )


def register_db_reclaim_handler(pool: DbPool) -> None:
    """Construct + register the reclaimer on the process registry."""
    handler = DbReclaimHandler(pool=pool)
    HandlerRegistry.instance().register(handler)
    log.scheduler.info(
        "[scheduler] db_reclaim handler registered",
        extra={"_fields": {"handler": handler.handler_name}},
    )
