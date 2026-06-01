"""Mutation helpers for :class:`JobScheduler` — ``update_job`` + ``run_now``.

Extracted from ``scheduler.py`` (B2 ≤300 lines) as free async functions taking
``(db, clock, registry, ...)`` so the scheduler keeps thin delegating methods,
mirroring how ``scheduler_helpers.py`` holds ``insert_job``/``compute_next_run``.

``run_now`` deliberately mirrors the poller's (:meth:`JobScheduler._run_job`)
state machine so an out-of-band run and a concurrent poll tick can never both
dispatch the same job: it performs the SAME in-flight ``pending → running``
compare-and-swap (a guarded ``UPDATE ... WHERE status='pending'``) and only
dispatches if it won that transition. Disabled/paused jobs are rejected outright
— a cron tick with no user behind it must not execute a job the user paused.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from stackowl.db.pool import DbPool
from stackowl.infra.clock import Clock
from stackowl.infra.observability import log
from stackowl.scheduler.base import HandlerRegistry
from stackowl.scheduler.job import Job, JobResult
from stackowl.scheduler.scheduler_helpers import compute_next_run, row_to_job, write_audit

# Ownership tags that a caller must never be able to rewrite via a params merge
# (NIT-2): clobbering these would let a future caller re-attribute a job and
# dodge the ownership gate / soft cap. Stripped from any update params payload.
_PROTECTED_PARAM_KEYS: frozenset[str] = frozenset({"owl", "created_by"})


async def update_job(
    db: DbPool,
    job_id: str,
    *,
    schedule: str | None = None,
    goal: str | None = None,
    params: dict[str, object] | None = None,
) -> Job | None:
    """Update a job's schedule/goal/params in place.

    Recomputes ``next_run_at`` only when ``schedule`` changes (B9: the scheduler
    owns the next-run arithmetic). Ownership tags (``owl``/``created_by``) are
    stripped from ``params`` before the merge so they can never be overwritten.
    Returns the reloaded :class:`Job`, or ``None`` when ``job_id`` is unknown.
    """
    log.scheduler.debug(
        "[scheduler] update_job: entry",
        extra={"_fields": {"job_id": job_id, "has_schedule": schedule is not None}},
    )
    rows = await db.fetch_all("SELECT * FROM jobs WHERE job_id = ?", (job_id,))
    if not rows:
        log.scheduler.warning(
            "[scheduler] update_job: job not found",
            extra={"_fields": {"job_id": job_id}},
        )
        return None
    current = row_to_job(rows[0])
    new_schedule = schedule if schedule is not None else current.schedule
    merged_params = dict(current.params)
    if params is not None:
        safe = {k: v for k, v in params.items() if k not in _PROTECTED_PARAM_KEYS}
        if len(safe) != len(params):
            log.scheduler.warning(
                "[scheduler] update_job: dropped protected param keys",
                extra={"_fields": {"job_id": job_id}},
            )
        merged_params.update(safe)
    if goal is not None:
        merged_params["goal"] = goal
    next_run = compute_next_run(new_schedule) if schedule is not None else current.next_run_at
    await db.execute(
        "UPDATE jobs SET schedule = ?, next_run_at = ?, params = ? WHERE job_id = ?",
        (
            new_schedule,
            next_run,
            json.dumps(merged_params, separators=(",", ":"), sort_keys=True),
            job_id,
        ),
    )
    await write_audit(db, "job_updated", job_id, details={"next_run_at": next_run})
    reloaded = await db.fetch_all("SELECT * FROM jobs WHERE job_id = ?", (job_id,))
    log.scheduler.info(
        "[scheduler] update_job: exit",
        extra={"_fields": {"job_id": job_id, "next_run_at": next_run}},
    )
    return row_to_job(reloaded[0]) if reloaded else None


async def run_now(
    db: DbPool,
    clock: Clock,
    registry: HandlerRegistry,
    job_id: str,
) -> JobResult | None:
    """Execute a single job's handler immediately, out of band.

    Refuses disabled/paused jobs, wins-or-loses the same ``pending → running``
    in-flight transition the poller uses (so the poll loop and run-now can never
    double-dispatch), runs the handler, then restores the job to its proper next
    state. Returns ``None`` when ``job_id`` is unknown; a :class:`JobResult` with
    ``success=False`` and a structured ``error`` when the run is rejected.
    """
    log.scheduler.debug("[scheduler] run_now: entry", extra={"_fields": {"job_id": job_id}})
    rows = await db.fetch_all("SELECT * FROM jobs WHERE job_id = ?", (job_id,))
    if not rows:
        log.scheduler.warning(
            "[scheduler] run_now: job not found",
            extra={"_fields": {"job_id": job_id}},
        )
        return None
    job = row_to_job(rows[0])

    if not job.enabled:
        log.scheduler.warning(
            "[scheduler] run_now: rejected — job disabled/paused",
            extra={"_fields": {"job_id": job_id, "status": job.status}},
        )
        return _rejected(job_id, "job is paused/disabled — resume it before running")

    # In-flight CAS: only the dispatcher that flips pending→running may run. If
    # the poller already claimed it (status != 'pending'), we lose and reject —
    # no double dispatch. The pool serialises a single connection, so a
    # ``SELECT changes()`` straight after the guarded UPDATE reports exactly how
    # many rows that UPDATE touched (1 = we won, 0 = lost / not pending).
    await db.execute(
        "UPDATE jobs SET status = 'running' WHERE job_id = ? AND status = 'pending'",
        (job_id,),
    )
    if not await _won_transition(db):
        log.scheduler.warning(
            "[scheduler] run_now: rejected — job not pending (lost transition)",
            extra={"_fields": {"job_id": job_id, "status": job.status}},
        )
        return _rejected(job_id, f"job is not runnable now (status '{job.status}')")

    handler = registry.get(job.handler_name)
    if handler is None:
        log.scheduler.error(
            "[scheduler] run_now: unknown handler",
            extra={"_fields": {"job_id": job_id, "handler": job.handler_name}},
        )
        await _restore_after_run(db, job_id, job.schedule)
        return _rejected(job_id, f"no handler registered for '{job.handler_name}'")

    t0 = clock.monotonic()
    try:
        result = await handler.execute(job)
    except Exception as exc:  # B5 — structured, never propagate
        duration_ms = (clock.monotonic() - t0) * 1000
        log.scheduler.error(
            "[scheduler] run_now: handler raised",
            exc_info=exc,
            extra={"_fields": {"job_id": job_id, "duration_ms": duration_ms}},
        )
        result = JobResult(
            job_id=job_id, success=False, output=None, error=str(exc), duration_ms=duration_ms
        )

    await _record_run(db, job, result, (clock.monotonic() - t0) * 1000)
    await _restore_after_run(db, job_id, job.schedule)
    await write_audit(db, "job_run_now", job_id, details={"success": result.success})
    log.scheduler.info(
        "[scheduler] run_now: exit",
        extra={"_fields": {"job_id": job_id, "success": result.success}},
    )
    return result


async def _won_transition(db: DbPool) -> bool:
    """True if the immediately-preceding guarded UPDATE flipped exactly one row.

    Reads SQLite's ``changes()`` on the same (single, serialised) connection the
    UPDATE just committed on, so it reflects that statement's affected rowcount.
    """
    rows = await db.fetch_all("SELECT changes() AS n")
    if not rows:
        return False
    try:
        return int(rows[0].get("n", 0)) == 1
    except (TypeError, ValueError):  # B5 — defensive, never raise out
        log.scheduler.warning("[scheduler] run_now: changes() unreadable — treating as lost")
        return False


def _rejected(job_id: str, reason: str) -> JobResult:
    return JobResult(job_id=job_id, success=False, output=None, error=reason, duration_ms=0.0)


async def _record_run(db: DbPool, job: Job, result: JobResult, duration_ms: float) -> None:
    """Write a ``job_runs`` row for this out-of-band run (mirrors the poller).

    Keys the row to the occurrence (``idempotency_key@next_run_at``) exactly as
    :meth:`JobScheduler._occurrence_key` does, so an out-of-band run cannot
    poison the dedup lookup for a future poll at a different scheduled instant.
    """
    now_iso = datetime.now(UTC).isoformat()
    run_id = str(uuid.uuid4())
    status = "completed" if result.success else "failed"
    occurrence_key = f"{job.idempotency_key}@{job.next_run_at}"
    await db.execute(
        "INSERT INTO job_runs (run_id, job_id, idempotency_key, status, duration_ms, ran_at) "
        "VALUES (?,?,?,?,?,?)",
        (run_id, job.job_id, occurrence_key, status, duration_ms, now_iso),
    )


async def _restore_after_run(db: DbPool, job_id: str, schedule: str) -> None:
    """Return a still-present recurring job to ``pending`` + next slot.

    A ``run_once`` handler deletes its own row; if the row is gone we leave that
    stand. Otherwise recompute ``next_run_at`` and set ``status='pending'`` so
    the poller picks it up at its proper next slot (mirrors ``_mark_completed``).
    """
    rows = await db.fetch_all("SELECT job_id FROM jobs WHERE job_id = ?", (job_id,))
    if not rows:
        return
    next_run = compute_next_run(schedule)
    now_iso = datetime.now(UTC).isoformat()
    await db.execute(
        "UPDATE jobs SET status = 'pending', last_run_at = ?, next_run_at = ? WHERE job_id = ?",
        (now_iso, next_run, job_id),
    )
