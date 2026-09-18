"""JournalPruneHandler -- give the journal its own decay leg (AD-6, Story 2.11).

``journal_events`` had no retention/prune mechanism at all: it grows
forever, and the only value that gestured at one
(``journal/retention.py``'s ``PROVISIONAL_JOURNAL_RETENTION_DAYS``) was an
admitted disconnected placeholder no production job read. This handler is
that job: it is the ONLY writer that ever issues ``DELETE FROM
journal_events`` (proven by ``tests/scheduler/handlers/test_journal_prune.py``'s
tripwire scan).

WHAT IT DOES, each hourly pass. Reads the LIVE settings (never
``journal/retention.py``'s tripwire-only constant -- see that module's
docstring for why the two must stay independent), computes the cutoff IN
PYTHON as ``datetime.now(UTC) - timedelta(days=retention_days)`` and BINDS
it as a query parameter -- never interpolated as SQL ``datetime('now',...)``,
which produces a space-separated string ("2026-08-19 15:20:00") that does
not compare correctly against ``occurred_at``'s own
``datetime.now(UTC).isoformat()`` format ("2026-08-19T10:00:00+00:00");
binding the same Python-isoformat shape as a parameter also keeps the new
``idx_journal_events_occurred_at`` index usable, which wrapping the column in
``datetime(occurred_at)`` would not. It excludes every cursor
:class:`~stackowl.journal.retention_holds.RetentionHoldRegistry` reports
held (Epic 3's future Needs-you items; the registry ships correct and
currently vacuous -- see ``retention_holds.py``'s docstring), and deletes in
bounded batches under ``PRAGMA secure_delete=ON`` -- reset back to ``OFF``
after the batch loop, in a ``finally``, because ``DbPool`` holds one
process-lifetime connection (``db/pool.py``) and a pragma left ON would
silently apply secure-delete overhead to every OTHER DELETE/UPDATE in the
app, not just this handler's own. It then runs ``PRAGMA
wal_checkpoint(TRUNCATE)`` -- unconditionally, even when nothing was pruned,
because the WAL can grow from ordinary journal writes between passes and
this is the one place that measures and reports it -- and stats the ``-wal``
sidecar file (offloaded via ``asyncio.to_thread``, matching
``health/contributors.py``'s own fix for a blocking ``Path.stat()`` inside
an ``async def``) to feed :func:`~stackowl.journal.health.note_wal_size`.

A FAILURE HERE NEVER FLOWS THROUGH ``journal.health.note_failure`` -- that
function's own docstring reserves it for ``recorder.py``'s ``journal.record()``
failures. Conflating the two would show an operator "journal.record has
failed N times" for what is actually a prune failure, and a later unrelated
successful ``record()`` call would silently clear the still-broken prune's
signal via ``note_success()``. This handler owns its own state
(``note_prune_failure``/``prune_consecutive_failures``) and its own
``health_check()`` branch instead.

Mirrors ``db_reclaim.py``'s full shape deliberately: same 4-point logging,
same batched-subquery delete style, same ``_PRUNE_BATCH``/``_PRUNE_MAX_PER_PASS``
caps, same ``register_*`` factory, same never-raise contract -- a maintenance
sweep that can take the platform down is worse than the journal it cleans.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from stackowl.db.pool import DbPool
from stackowl.infra.observability import log
from stackowl.journal.health import note_prune_failure, note_prune_success, note_wal_size
from stackowl.journal.retention_holds import get_retention_hold_registry
from stackowl.scheduler.base import HandlerRegistry, JobHandler
from stackowl.scheduler.job import Job, JobResult

#: Rows deleted per statement -- same reasoning as ``db_reclaim.py``'s
#: ``_PRUNE_BATCH``: a single DELETE over a large backlog holds SQLite's
#: write lock for the whole statement, and this repo has already paid for
#: "database is locked" events from exactly that shape.
_PRUNE_BATCH = 5_000

#: Ceiling per hourly pass -- mirrors ``db_reclaim.py``'s
#: ``_PRUNE_MAX_PER_PASS``: a backlog clears over a handful of ticks instead
#: of one long one.
_PRUNE_MAX_PER_PASS = 50_000


class JournalPruneHandler(JobHandler):
    """Prune ``journal_events`` rows past retention, then checkpoint the WAL."""

    def __init__(self, pool: DbPool, db_path: Path) -> None:
        self._pool = pool
        self._db_path = db_path

    @property
    def handler_name(self) -> str:
        return "journal_prune"

    async def _wal_bytes(self) -> int:
        """Size of the ``-wal`` sidecar file. 0 when it does not exist -- a
        checkpoint that fully drained the WAL removes the file entirely, and
        that is success, not a measurement failure. The blocking ``stat()``
        call is offloaded via ``asyncio.to_thread`` -- mirrors
        ``health/contributors.py``'s own fix for the same shape (B9: no
        blocking I/O on the event loop)."""
        wal_path = Path(str(self._db_path) + "-wal")
        try:
            stat = await asyncio.to_thread(wal_path.stat)
            return stat.st_size
        except FileNotFoundError:
            # INFO, not DEBUG -- production writes no DEBUG at all
            # (tests/audit/test_a_background_subsystem_that_declines_still_says_so.py),
            # and this is a real, if healthy, outcome: the WAL was fully
            # checkpointed away since the last pass.
            log.scheduler.info(
                "[scheduler] journal_prune: no -wal sidecar file -- treating "
                "as 0 bytes",
                extra={"_fields": {"wal_path": str(wal_path)}},
            )
            return 0
        except OSError as exc:
            # Broader than FileNotFoundError deliberately -- a PermissionError
            # or any other OSError statting the sidecar must not escape to the
            # OUTER except in execute() and misreport an otherwise-successful
            # prune+checkpoint pass as a total failure. Logged loudly (never a
            # silent bare except) and treated the same as "file absent": 0.
            log.scheduler.warning(
                "[scheduler] journal_prune: could not stat the -wal sidecar "
                "file -- reporting 0 bytes this pass",
                exc_info=exc, extra={"_fields": {"wal_path": str(wal_path)}},
            )
            return 0

    async def execute(self, job: Job) -> JobResult:
        # 1. ENTRY
        t0 = time.monotonic()
        deleted_so_far = 0
        secure_delete_set = False

        try:
            # Settings() construction happens INSIDE the try -- a failure here
            # must still produce a failed-but-non-raising JobResult, not
            # escape uncaught and break this handler's never-fail-a-tick
            # contract.
            from stackowl.config.settings import Settings

            settings = Settings().journal
            retention_days = settings.retention_days
            wal_budget_bytes = settings.wal_size_budget_bytes
            log.scheduler.info(
                "[scheduler] journal_prune.execute: entry",
                extra={"_fields": {
                    "job_id": job.job_id,
                    "retention_days": retention_days,
                    "wal_size_budget_bytes": wal_budget_bytes,
                }},
            )

            held = await get_retention_hold_registry().held_cursors()
            # The cutoff is computed in PYTHON, in the exact
            # `datetime.now(UTC).isoformat()` shape `journal/models.py`'s
            # `_now_iso()` stamps every `occurred_at` with, and bound as a
            # parameter -- never interpolated as SQL `datetime('now',...)`,
            # which produces a space-separated string that does not compare
            # correctly against the T-separated `occurred_at` column (see
            # module docstring). Binding the raw column also keeps
            # `idx_journal_events_occurred_at` usable.
            cutoff_iso = (datetime.now(UTC) - timedelta(days=retention_days)).isoformat()
            exclude_sql = ""
            exclude_params: tuple[int, ...] = ()
            if held:
                placeholders = ",".join("?" for _ in held)
                exclude_sql = f" AND cursor NOT IN ({placeholders})"
                exclude_params = tuple(held)

            before = await self._pool.fetch_all(
                "SELECT COUNT(*) AS n FROM journal_events WHERE occurred_at < ?"
                f"{exclude_sql}",
                (cutoff_iso, *exclude_params),
            )
            eligible = int(before[0]["n"]) if before else 0
            pruned_count = min(eligible, _PRUNE_MAX_PER_PASS)

            # 2. DECISION -- only touch `secure_delete` and run the batch loop
            # when there is actually something to delete; a pointless PRAGMA
            # toggle + zero-row loop every hourly tick is churn for nothing.
            if pruned_count:
                # PRAGMA secure_delete=ON so freed pages are overwritten
                # rather than merely unlinked (AD-4's metadata-only contract
                # extends to what a raw file read of a deleted row could
                # still show). `DbPool` holds ONE process-lifetime connection
                # (db/pool.py), so this is reset back to OFF in the `finally`
                # below -- left ON, it would silently apply secure-delete
                # overhead to every OTHER DELETE/UPDATE in the app forever.
                await self._pool.execute("PRAGMA secure_delete=ON", ())
                secure_delete_set = True
                try:
                    # 3. STEP -- delete in bounded batches (`_PRUNE_BATCH`),
                    # capped per pass at `_PRUNE_MAX_PER_PASS`. A single
                    # DELETE over the whole backlog would hold SQLite's write
                    # lock for its entire duration -- this repo has already
                    # paid for "database is locked" events from that shape.
                    remaining = pruned_count
                    while remaining > 0:
                        batch = min(_PRUNE_BATCH, remaining)
                        await self._pool.execute(
                            "DELETE FROM journal_events WHERE cursor IN ("
                            "  SELECT cursor FROM journal_events WHERE "
                            f"  occurred_at < ?{exclude_sql}"
                            "  ORDER BY cursor LIMIT ?)",
                            (cutoff_iso, *exclude_params, batch),
                        )
                        deleted_so_far += batch
                        remaining -= _PRUNE_BATCH
                finally:
                    if secure_delete_set:
                        await self._pool.execute("PRAGMA secure_delete=OFF", ())

            # Checkpoint runs even when nothing was pruned -- ordinary
            # journal writes between passes grow the WAL too, and this is
            # the one place that measures it.
            checkpoint_rows = await self._pool.fetch_all(
                "PRAGMA wal_checkpoint(TRUNCATE)", (),
            )
            row = checkpoint_rows[0] if checkpoint_rows else {}
            values = list(row.values())
            busy = int(values[0]) if len(values) > 0 else 0
            log_frames = int(values[1]) if len(values) > 1 else 0
            checkpointed_frames = int(values[2]) if len(values) > 2 else 0

            wal_bytes = await self._wal_bytes()
            note_wal_size(wal_bytes, wal_budget_bytes)
            # A clean pass resets journal_prune's OWN failure streak -- mirrors
            # recorder.py's note_success() shape for its own, separate streak
            # (see module docstring for why the two must stay independent).
            note_prune_success()
        except Exception as exc:  # noqa: BLE001 -- maintenance may not fail a tick
            duration_ms = (time.monotonic() - t0) * 1000
            remedy = (
                "journal_prune failed -- journal_events keeps growing until "
                "the next hourly pass; check the log for the underlying error"
            )
            log.scheduler.error(
                "[scheduler] journal_prune.execute: failed",
                exc_info=exc, extra={"_fields": {"job_id": job.job_id}},
            )
            # A prune-job failure is NOT a journal.record() failure -- see
            # module docstring. Its own state/signal, never the shared
            # note_failure() recorder.py owns.
            note_prune_failure(remedy)
            return JobResult(
                job_id=job.job_id, effect_class="state_change", success=False,
                output="", error=f"journal_prune failed: {exc}",
                duration_ms=duration_ms,
                # The REAL count of rows actually deleted before the failure
                # -- a checkpoint/WAL-stat failure AFTER a successful delete
                # loop must not misreport real progress as zero.
                metadata={"pruned_count": deleted_so_far},
            )

        duration_ms = (time.monotonic() - t0) * 1000
        # 4. EXIT
        log.scheduler.info(
            "[scheduler] journal_prune.execute: exit",
            extra={"_fields": {
                "job_id": job.job_id,
                "pruned_count": pruned_count,
                "busy": busy,
                "log_frames": log_frames,
                "checkpointed_frames": checkpointed_frames,
                "wal_bytes": wal_bytes,
                "wal_size_budget_bytes": wal_budget_bytes,
                "held_count": len(held),
                "duration_ms": duration_ms,
            }},
        )
        return JobResult(
            job_id=job.job_id, effect_class="state_change", success=True,
            output=f"pruned_count={pruned_count} wal_bytes={wal_bytes}",
            error=None, duration_ms=duration_ms,
            metadata={
                "pruned_count": pruned_count,
                "busy": busy,
                "log_frames": log_frames,
                "checkpointed_frames": checkpointed_frames,
                "wal_bytes": wal_bytes,
            },
        )


def register_journal_prune_handler(pool: DbPool, db_path: Path) -> None:
    """Construct + register the journal pruner on the process registry."""
    handler = JournalPruneHandler(pool=pool, db_path=db_path)
    HandlerRegistry.instance().register(handler)
    log.scheduler.info(
        "[scheduler] journal_prune handler registered",
        extra={"_fields": {"handler": handler.handler_name}},
    )
