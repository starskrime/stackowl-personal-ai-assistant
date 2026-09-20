"""CommandResumeSweepHandler -- the seeded job that resumes a parked COMMAND
task once its Needs-you item is answered (Story 4.4, AD-27/AD-28).

EXACTLY ONE SEED, EXACTLY ONE HANDLER. Mirrors
``needs_you_expiry_sweep.py``'s own shape deliberately: same constructor
taking ``pool: DbPool``, same 4-point logging, same never-raise-out-of-
``execute()`` contract (a maintenance sweep that can take the platform down
is worse than the rows it leaves parked one more minute), same
``register_*`` factory seeded idempotently by ``_seed_minutes_schedule`` in
``scheduler/assembly.py`` -- never a second seed call, never an
unconditional insert.

CADENCE: 1 minute, the SAME as ``needs_you_expiry_sweep`` -- a parked
command is exactly as visible/time-sensitive as a Needs-you item's own
expiry (the two are opposite sides of the same durable wait), not a
bulk-maintenance job like ``journal_prune``/``db_reclaim`` where an hourly
pass is plenty.
"""

from __future__ import annotations

import time

from stackowl.db.pool import DbPool
from stackowl.infra.observability import log
from stackowl.pipeline.durable.command_resume_sweep import (
    resume_resolved_parked_commands,
)
from stackowl.scheduler.base import HandlerRegistry, JobHandler
from stackowl.scheduler.job import Job, JobResult


class CommandResumeSweepHandler(JobHandler):
    """Periodic sweep: resume every parked COMMAND task whose bound
    Needs-you item has settled, through
    :func:`stackowl.pipeline.durable.command_resume_sweep.
    resume_resolved_parked_commands` -- the SAME durable-state read a boot-
    time pass (``startup/orchestrator.py``) also uses, never a bespoke
    in-memory registry of "what is waiting"."""

    def __init__(self, pool: DbPool) -> None:
        self._pool = pool

    @property
    def handler_name(self) -> str:
        return "command_resume_sweep"

    async def execute(self, job: Job) -> JobResult:
        # 1. ENTRY
        t0 = time.monotonic()
        log.scheduler.info(
            "[scheduler] command_resume_sweep.execute: entry",
            extra={"_fields": {"job_id": job.job_id}},
        )
        try:
            # 2. DECISION / 3. STEP -- one call; resume_resolved_parked_commands
            # itself never raises per-row (it logs and skips), so the only way
            # this try can fail is an infrastructure problem with the pool
            # itself (e.g. the candidate SELECT failing outright).
            resumed_ids = await resume_resolved_parked_commands(self._pool)
        except Exception as exc:  # noqa: BLE001 -- maintenance may not fail a tick
            duration_ms = (time.monotonic() - t0) * 1000
            log.scheduler.error(
                "[scheduler] command_resume_sweep.execute: failed",
                exc_info=exc, extra={"_fields": {"job_id": job.job_id}},
            )
            return JobResult(
                job_id=job.job_id, effect_class="state_change", success=False,
                output="", error=f"command_resume_sweep failed: {exc}",
                duration_ms=duration_ms,
                metadata={"resumed_count": 0},
            )

        duration_ms = (time.monotonic() - t0) * 1000
        # 4. EXIT
        log.scheduler.info(
            "[scheduler] command_resume_sweep.execute: exit",
            extra={"_fields": {
                "job_id": job.job_id, "resumed_count": len(resumed_ids),
                "duration_ms": duration_ms,
            }},
        )
        return JobResult(
            job_id=job.job_id, effect_class="state_change", success=True,
            output=f"resumed_count={len(resumed_ids)}",
            error=None, duration_ms=duration_ms,
            metadata={"resumed_count": len(resumed_ids), "resumed_ids": resumed_ids},
        )


def register_command_resume_sweep_handler(pool: DbPool) -> None:
    """Construct + register the command-resume-sweep handler on the process
    registry."""
    handler = CommandResumeSweepHandler(pool=pool)
    HandlerRegistry.instance().register(handler)
    log.scheduler.info(
        "[scheduler] command_resume_sweep handler registered",
        extra={"_fields": {"handler": handler.handler_name}},
    )
