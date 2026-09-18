"""NeedsYouExpirySweepHandler -- the seeded job that expires stale Needs-you
items (Story 3.2, AD-28).

WHY A SEEDED JOB AT ALL, ON TOP OF ``open_items()``'s OWN OPPORTUNISTIC
SWEEP. ``open_items()`` only sweeps when something actually calls it -- an
item with no reader (the Needs-you strip closed, nobody polling) would stay
open past its ``expires_at`` forever with no seeded job of its own. AC5:
"no expired item stays open in the strip" needs a sweep that runs on its OWN
clock, not just piggy-backed on a read.

EXACTLY ONE SEED, EXACTLY ONE HANDLER. Mirrors ``journal_prune.py``'s own
shape deliberately: same constructor taking ``pool: DbPool``, same 4-point
logging, same never-raise-out-of-``execute()`` contract (a maintenance sweep
that can take the platform down is worse than the items it expires), same
``register_*`` factory seeded idempotently by ``_seed_minutes_schedule`` in
``scheduler/assembly.py`` -- never a second seed call, never an unconditional
insert (spec Boundaries).

CADENCE: 1 minute, not ``journal_prune``'s hourly. This is a UI-responsiveness
handler (an item stuck open past its ``expires_at`` is visibly wrong in the
Needs-you strip), the same reasoning ``objective_driver`` uses for its own
1-minute cadence -- not a bulk-maintenance job like ``journal_prune`` or
``db_reclaim``, where an hourly pass is plenty.
"""

from __future__ import annotations

import time

from stackowl.db.pool import DbPool
from stackowl.infra.observability import log
from stackowl.journal import needs_you
from stackowl.scheduler.base import HandlerRegistry, JobHandler
from stackowl.scheduler.job import Job, JobResult


class NeedsYouExpirySweepHandler(JobHandler):
    """Periodic sweep: expire every open Needs-you item whose ``expires_at``
    has passed, through :func:`stackowl.journal.needs_you.sweep_expired_items`
    -- the SAME conditional-UPDATE resolver path a real owner answer uses
    (AD-28), never a bespoke bulk UPDATE of its own."""

    def __init__(self, pool: DbPool) -> None:
        self._pool = pool

    @property
    def handler_name(self) -> str:
        return "needs_you_expiry_sweep"

    async def execute(self, job: Job) -> JobResult:
        # 1. ENTRY
        t0 = time.monotonic()
        log.scheduler.info(
            "[scheduler] needs_you_expiry_sweep.execute: entry",
            extra={"_fields": {"job_id": job.job_id}},
        )
        try:
            # 2. DECISION / 3. STEP -- one call; sweep_expired_items itself
            # never raises per-item (it logs and skips), so the only way this
            # try can fail is an infrastructure problem with the pool itself
            # (e.g. the SELECT of candidates failing outright).
            expired_ids = await needs_you.sweep_expired_items(self._pool)
        except Exception as exc:  # noqa: BLE001 -- maintenance may not fail a tick
            duration_ms = (time.monotonic() - t0) * 1000
            log.scheduler.error(
                "[scheduler] needs_you_expiry_sweep.execute: failed",
                exc_info=exc, extra={"_fields": {"job_id": job.job_id}},
            )
            return JobResult(
                job_id=job.job_id, effect_class="state_change", success=False,
                output="", error=f"needs_you_expiry_sweep failed: {exc}",
                duration_ms=duration_ms,
                metadata={"expired_count": 0},
            )

        duration_ms = (time.monotonic() - t0) * 1000
        # 4. EXIT
        log.scheduler.info(
            "[scheduler] needs_you_expiry_sweep.execute: exit",
            extra={"_fields": {
                "job_id": job.job_id, "expired_count": len(expired_ids),
                "duration_ms": duration_ms,
            }},
        )
        return JobResult(
            job_id=job.job_id, effect_class="state_change", success=True,
            output=f"expired_count={len(expired_ids)}",
            error=None, duration_ms=duration_ms,
            metadata={"expired_count": len(expired_ids), "expired_ids": expired_ids},
        )


def register_needs_you_expiry_sweep_handler(pool: DbPool) -> None:
    """Construct + register the expiry-sweep handler on the process registry."""
    handler = NeedsYouExpirySweepHandler(pool=pool)
    HandlerRegistry.instance().register(handler)
    log.scheduler.info(
        "[scheduler] needs_you_expiry_sweep handler registered",
        extra={"_fields": {"handler": handler.handler_name}},
    )
