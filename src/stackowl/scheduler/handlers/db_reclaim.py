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

AND THAT REPORT WAS NOT ENOUGH — MEASURED 2026-09-03. Both self-checks above ask
about CONFIGURATION, and the live database defeated both at once::

    reclaimed_pages: 1      every tick, 146 consecutive hourly ticks
    free_ratio:      0.22   against a 0.25 threshold, so no warning
    auto_vacuum:     2      so needs_one_time_vacuum() answered "fine"
    file_mb:         359.4  unchanged for 8+ hours

``PRAGMA incremental_vacuum(N)`` returned exactly ONE page per call — whatever N
was, whatever the argument form, stepped or not — reproduced on a copy of the
live file, while 19,285 pages (79 MB, 22%) sat on the freelist. A full ``VACUUM``
of that copy recovered 82 MB (359.4 -> 277.3 MB). Note this database HAD already
been converted (the hand VACUUM below), so conversion is not the explanation; the
incremental path is simply ineffective here, and the cause of THAT is not yet
established.

:func:`reclaim_stalled` is the check that does not care why. It asks whether the
pass got what it asked for when the freelist could have supplied it — a question
about the RESULT, which stays true however SQLite's reasons change.

ONE THING IT CANNOT DO, stated so nobody is surprised: switching a database from
``auto_vacuum=NONE`` to ``INCREMENTAL`` requires one full ``VACUUM`` to take
effect. A database created before this shipped reclaims NOTHING until that
happens once. ``pool.py`` sets the pragma so every NEW database is born correct;
the live one was converted by hand on 2026-08-22 (922 MB -> 273 MB in 35s).
:func:`needs_one_time_vacuum` exists so the condition is detectable rather than
folklore — but it detects only the MODE, and 2026-09-03 showed a database in the
right mode that still reclaims nothing, so it is necessary and not sufficient.

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


def reclaim_stalled(*, asked: int, reclaimed: int, free_after: int) -> bool:
    """True when a pass could have reclaimed far more than it did.

    ASKS ABOUT THE RESULT, NOT THE CONFIGURATION — which is the whole point.
    This handler's other two self-checks both ask about configuration:
    :func:`needs_one_time_vacuum` reads ``PRAGMA auto_vacuum``, and the
    "falling behind" alarm reads a free RATIO. MEASURED 2026-09-03, the live
    database defeated both: ``auto_vacuum`` reported 2 (INCREMENTAL) so the first
    said "fine", and the free ratio sat at 0.22 against a 0.25 threshold so the
    second never fired — while 146 consecutive hourly ticks each asked for 2,000
    pages and reclaimed exactly ONE, leaving 19,285 pages (79 MB, 22% of the
    file) parked. A full VACUUM of a copy recovered 82 MB the incremental path
    could not reach.

    The handler already computed the proof — ``reclaimed = pages_before -
    pages_after``, logged at INFO every tick — and nothing ever compared it to
    what was asked for.

    NO MAGIC NUMBER. If the freelist held at least as many pages as the pass
    requested and the pass did not get them, reclaim is not working — true
    whatever the cause, so this keeps holding if the reason changes.
    """
    if asked <= 0:
        return False
    return free_after >= asked and reclaimed < asked


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


#: How long a completed run stays in ``job_runs``.
#:
#: 7 DAYS, chosen by Bakir on 2026-09-02 when the numbers were put to him: the
#: table held 255,363 completed rows — 45.1 MB plus a 19.1 MB idempotency index,
#: 19% of a 342 MB database — and 223,266 of them (87%) are older than a week.
#:
#: IT SHIPPED AT 100 FIRST, deliberately: 100 days deleted NOTHING (the oldest row
#: was 92 days old) so the unbounded append was capped without this loop deleting
#: his data unasked, which its own rules make a stop-and-brief. This is that
#: authorisation arriving.
#:
#: SAFE BY CONSTRUCTION, not by judgement. ``job_runs`` has exactly ONE reader —
#: the exactly-once guard in ``scheduler._dispatch`` — and it looks up
#: ``_occurrence_key``: ``{job.idempotency_key}@{job.next_run_at}``. The key EMBEDS
#: the scheduled instant and every one of the 255,363 in the live table is
#: distinct, so once an instant has passed its key can never be queried again.
#: There is no time window in the guard for this to shorten.
_RUN_HISTORY_RETENTION_DAYS = 7

#: THE SECOND TABLE NOTHING HAD EVER BOUNDED, and the window is MEASURED rather
#: than chosen (DEBT-292).
#:
#: `approach_rating_pending` holds one row per qualifying answer awaiting a
#: thumbs-up/down tap, and its own store said, in a comment: *"no size cap / TTL
#: sweep — a DB row is small and rare (one per qualifying Telegram answer, cleared
#: on tap), so an unbounded table is fine for now. Add a periodic
#: delete-older-than-N-days sweep IF UNTAPPED VOTES EVER ACCUMULATE."*
#:
#: THEY ACCUMULATED, AND NOTHING RE-READ THE CONDITION THE NOTE ITSELF NAMED.
#: MEASURED 2026-09-10: **1,480 pending rows spanning 2026-07-13 to today** against
#: **61 ratings ever recorded** — "cleared on tap" clears 4% of what it writes, and
#: 1,425 of those rows are already older than this window.
#:
#: WHY SEVEN DAYS IS NOT A GUESS. Every tap in the retained corpus was timed
#: against its own answer's `captured_at`: 13 taps, latencies 11s, 15s, 27s, 29s,
#: 48s, 52s, 55s, 1m18, 1m25, 1m57, 2m07, 2m42 and **3m32 — the maximum**. Not one
#: tap in 1,541 offers arrived more than four minutes after the offer. Seven days is
#: ~2,800x that maximum, and MATCHES ITS NEIGHBOUR above rather than being tuned
#: separately: given the evidence every value from hours upward is equivalent, so
#: the useful property is that this codebase has one retention window, not two.
#:
#: AND DELETING A ROW CANNOT LOSE A VOTE — read from the handler, not assumed.
#: `ApproachRatingHandler.handle` records the vote by calling
#: `set_approach_rating(trace_id, vote)` against `task_outcomes` DIRECTLY; the
#: pending row is consulted only afterwards, for the message LOCATION used to edit
#: the Telegram message. `get_message` returning None is an existing, logged path
#: ("vote recorded but no message location — edit skipped") which has already fired
#: in this corpus. So a tap on a swept row still records the vote; only the cosmetic
#: edit is skipped.
_RATING_PENDING_RETENTION_DAYS = 7

#: Rows deleted per statement. A single DELETE over 223,266 rows holds SQLite's
#: write lock for the whole statement, and this repo has already paid for
#: database-is-locked events — one contention moment emits four of them. Batching
#: keeps each lock short; the pass simply takes several.
_PRUNE_BATCH = 5_000

#: Ceiling per hourly pass. The backlog clears over a handful of ticks instead of
#: one long one, and steady state (a few hundred rows an hour) is far below it, so
#: this only ever engages while catching up.
_PRUNE_MAX_PER_PASS = 50_000


class DbReclaimHandler(JobHandler):
    """Hand freed SQLite pages back to the operating system, a chunk at a time.

    Optional job ``params``: ``{"max_pages": 2000}``.
    """

    def __init__(self, pool: DbPool) -> None:
        self._pool = pool

    @property
    def handler_name(self) -> str:
        return "db_reclaim"

    async def _prune_run_history(self) -> int:
        """Bound ``job_runs``, which nothing had ever bounded. Never raises.

        MEASURED 2026-09-02: 252,905 rows, every one ``status='completed'``,
        spanning 2026-06-02 to today — **45.1 MB of table plus 19.1 MB of
        idempotency index, 19% of a 342 MB database**. The largest single
        contributor is ``objective_driver`` at 67,551 runs, firing every minute
        against a table that has zero rows. Nothing had ever deleted one of these.

        THE WINDOW, AND THE ARGUMENT THAT DELETING IS SAFE, ARE STATED ONCE — on
        ``_RUN_HISTORY_RETENTION_DAYS``, together with the sequence by which the
        operator authorised it. Read them there. Do not restate them here.

        THAT INSTRUCTION IS A FIX, not tidiness, so it is worth the lines. Both
        were stated in this docstring as well until 2026-09-09, and `c628d1bf`
        tightened the window on the operator's authority by rewriting only the
        constant's block. This one went on describing the deliberately loose
        window that predated his authorisation, and on saying that tightening it
        was a decision escalated to him rather than taken. Eleven passes had by
        then deleted his rows — 3,909, 4,087 and 4,151 on the last three, each
        logging ``retention_days: 7`` — so the method that performs the deletion
        documented itself as having declined to. The duplicated safety argument
        had drifted as well, citing 252,905 rows where the constant cites 255,363.

        Syncing the two blocks would have left the trap armed for the next
        change; removing one of them is what disarms it.
        `scripts/superseded_constants.py` now finds prose anywhere in ``src/``
        that states a value its own constant no longer has.

        Returns:
            Rows deleted. 0 on any failure — maintenance may never fail a tick.
        """
        try:
            before = await self._pool.fetch_all(
                "SELECT COUNT(*) AS n FROM job_runs WHERE ran_at < "
                f"datetime('now','-{_RUN_HISTORY_RETENTION_DAYS} days')", (),
            )
            n = int(before[0]["n"]) if before else 0
            if not n:
                return 0
            # BATCHED, not one statement. See _PRUNE_BATCH: a single DELETE over
            # the 223,266-row backlog holds the write lock for its whole duration,
            # and a turn contending with it is exactly how this database produced
            # "database is locked" before.
            n = min(n, _PRUNE_MAX_PER_PASS)
            remaining = n
            while remaining > 0:
                await self._pool.execute(
                    "DELETE FROM job_runs WHERE run_id IN ("
                    "  SELECT run_id FROM job_runs WHERE ran_at < "
                    f"  datetime('now','-{_RUN_HISTORY_RETENTION_DAYS} days')"
                    "  LIMIT ?)",
                    (min(_PRUNE_BATCH, remaining),),
                )
                remaining -= _PRUNE_BATCH
            log.scheduler.info(
                "[scheduler] db_reclaim: pruned job run history — nothing had "
                "ever bounded this table",
                extra={"_fields": {
                    "deleted": n, "retention_days": _RUN_HISTORY_RETENTION_DAYS,
                }},
            )
            return n
        except Exception as exc:  # noqa: BLE001 — maintenance may not fail a tick
            log.scheduler.warning(
                "[scheduler] db_reclaim: could not prune job_runs — the table "
                "stays unbounded until the next tick",
                exc_info=exc,
            )
            return 0

    async def _known_principals(self) -> list[str]:
        """Principal ids the tenancy store holds. Empty list on any failure.

        Same shape as `DurableTaskStore._known_principals`, and asked for the same
        reason: a maintenance sweep over an OWNER-GOVERNED table has to name an
        owner per statement, and the only honest source of owners is the tenancy
        store rather than a default constant.
        """
        try:
            rows = await self._pool.fetch_all("SELECT principal_id FROM principals", ())
            return [str(r["principal_id"]) for r in rows or []]
        except Exception as exc:  # noqa: BLE001 — maintenance may not fail a tick
            log.scheduler.warning(
                "[scheduler] db_reclaim: could not read the principals table — "
                "skipping the rating sweep this tick",
                exc_info=exc,
            )
            return []

    async def _prune_untapped_ratings(self) -> int:
        """Bound ``approach_rating_pending``, the second table nothing bounded.

        THE WINDOW, THE MEASUREMENT BEHIND IT AND THE ARGUMENT THAT DELETING IS
        SAFE ARE STATED ONCE — on :data:`_RATING_PENDING_RETENTION_DAYS`. Read them
        there. Do not restate them here: the sibling above carries a paragraph
        explaining that a duplicated safety argument DRIFTED, and left the method
        that performs a deletion documenting itself as having declined to.

        PER OWNER, one statement each. `approach_rating_pending` carries `owner_id`
        and became visible to the owner-scope tripwire only in DEBT-291, which is
        exactly why this sweep is written this way rather than as one unscoped
        DELETE: a maintenance job is not an exemption from the tenancy boundary,
        and on a single-principal install the two forms are indistinguishable —
        which is how an unscoped one would have survived review.

        Unbatched, deliberately, and that is a property of the numbers rather than
        an omission: the whole backlog is 1,425 expired rows against a
        `_PRUNE_BATCH` of 5,000, so one statement holds the write lock for a
        fraction of what the `job_runs` sweep already holds it for. If this table
        ever reaches that size, the batching above is the shape to copy.

        Returns:
            Rows deleted across all owners. 0 on any failure — maintenance may
            never fail a tick.
        """
        cutoff = f"strftime('%s','now','-{_RATING_PENDING_RETENTION_DAYS} days')"
        deleted = 0
        for owner_id in await self._known_principals():
            try:
                before = await self._pool.fetch_all(
                    "SELECT COUNT(*) AS n FROM approach_rating_pending "
                    f"WHERE owner_id = ? AND created_at < {cutoff}",
                    (owner_id,),
                )
                n = int(before[0]["n"]) if before else 0
                if not n:
                    continue
                await self._pool.execute(
                    "DELETE FROM approach_rating_pending "
                    f"WHERE owner_id = ? AND created_at < {cutoff}",
                    (owner_id,),
                )
                deleted += n
            except Exception as exc:  # noqa: BLE001 — maintenance may not fail a tick
                log.scheduler.warning(
                    "[scheduler] db_reclaim: could not prune approach_rating_pending "
                    "for one owner — the table stays unbounded until the next tick",
                    exc_info=exc, extra={"_fields": {"owner_id": owner_id}},
                )
        if deleted:
            log.scheduler.info(
                "[scheduler] db_reclaim: pruned untapped approach ratings — the "
                "store's own comment asked for this once they accumulated",
                extra={"_fields": {
                    "deleted": deleted,
                    "retention_days": _RATING_PENDING_RETENTION_DAYS,
                }},
            )
        return deleted

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

        # 3. STEP — retention BEFORE reclaim, because incremental_vacuum can only
        # hand back pages something has already freed. Pruning after vacuuming
        # would leave the freed pages until the next hourly tick.
        pruned = await self._prune_run_history()
        pruned_ratings = await self._prune_untapped_ratings()

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

        # THE PASS DID NOT DO WHAT IT ASKED FOR, and that is knowable here without
        # any threshold. MEASURED 2026-09-03: 146 consecutive hourly ticks each
        # asked for 2,000 pages and reclaimed exactly ONE, while 19,285 pages
        # (79 MB, 22% of the file) sat free — and neither self-check below noticed,
        # because both ask about CONFIGURATION. `needs_one_time_vacuum` reads the
        # auto_vacuum MODE (2, so "fine") and the ratio alarm reads 0.25 against a
        # stuck 0.22. The number that proves it was already computed one line up.
        stalled = reclaim_stalled(
            asked=max_pages, reclaimed=reclaimed, free_after=free_after,
        )
        if stalled:
            log.scheduler.warning(
                "[scheduler] db_reclaim: the pass reclaimed far less than it asked "
                "for — incremental reclaim is not working on this database",
                extra={"_fields": {
                    "job_id": job.job_id,
                    "asked_pages": max_pages,
                    "reclaimed_pages": reclaimed,
                    "freelist_after": free_after,
                    "free_mb": round(page_size * free_after / 1e6, 1),
                    # The remedy this condition implies. A full VACUUM rewrites the
                    # database under an exclusive lock, which is exactly why this
                    # handler does bounded incremental work instead — so it is
                    # named here, not performed.
                    "remedy": "a one-time full VACUUM is required to reclaim this space",
                }},
            )

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
                # ASKED, alongside GOT. Without this pair on the same line the
                # exit log said "reclaimed_pages: 1" for 146 consecutive ticks and
                # nothing about it looked wrong — 1 is a fine number when you do
                # not know 2,000 was requested.
                "asked_pages": max_pages,
                "stalled": stalled,
                "freelist_after": free_after,
                "pruned_runs": pruned,
                "pruned_ratings": pruned_ratings,
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
                "asked_pages": max_pages,
                "stalled": stalled,
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
