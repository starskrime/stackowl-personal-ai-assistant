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
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from stackowl.db.pool import DbPool
from stackowl.infra.clock import Clock
from stackowl.infra.observability import log
from stackowl.journal import ActorKind, JournalEvent, Outcome, RecordRef
from stackowl.journal import record as journal_record
from stackowl.journal.job_events import JobFinishedAttrs, JobStartedAttrs
from stackowl.scheduler.base import HandlerRegistry
from stackowl.scheduler.job import Job, JobResult
from stackowl.scheduler.scheduler_helpers import (
    compute_next_run,
    delete_finished_one_shot,
    row_to_job,
    write_audit,
)
from stackowl.tools.verification import is_trustworthy_success

#: AD-4's bound, mirrors ``scheduler.py``'s own module-level constant.
_MAX_LABEL_LEN = 64


def _job_record_ref(job_id: str) -> RecordRef:
    """AD-4's record_ref for every ``job.*`` event -- mirrors
    ``scheduler.py``'s own helper of the same name."""
    return RecordRef(kind="sqlite", locator={"table": "jobs", "job_id": job_id})

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
    tz: str = "UTC",
) -> Job | None:
    """Update a job's schedule/goal/params in place.

    Recomputes ``next_run_at`` only when ``schedule`` changes (B9: the scheduler
    owns the next-run arithmetic), using ``tz`` (the user IANA timezone,
    ``settings.system.timezone``) — without it a ``daily@HH:MM`` edit silently
    re-arms in UTC instead of local time. Ownership tags (``owl``/``created_by``)
    are stripped from ``params`` before the merge so they can never be
    overwritten. Returns the reloaded :class:`Job`, or ``None`` when ``job_id``
    is unknown.
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
    next_run = (
        compute_next_run(new_schedule, tz=tz) if schedule is not None else current.next_run_at
    )
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
    *,
    tz: str = "UTC",
    settle: Callable[[Job, JobResult, float], Awaitable[None]],
) -> JobResult | None:
    """Execute a single job's handler immediately, out of band.

    Refuses disabled/paused jobs, wins-or-loses the same ``pending → running``
    in-flight transition the poller uses (so the poll loop and run-now can never
    double-dispatch), and runs the handler. Afterwards:

    * a RECURRING job returns to ``pending`` at its next slot, recomputed with ``tz``
      (the user IANA timezone) so it stays on its configured local time;
    * a ONE-SHOT is handed to ``settle`` — the poller's own disposition
      (``JobScheduler._settle``): retired when it succeeded, put on the retry ladder
      when it did not. If its retirement fails, its run is recorded completed and
      the row stays 'running' until a reaper frees it — to be retired, not re-run;
    * a one-shot whose run is ALREADY recorded completed (an earlier retirement that
      failed) is retired here and not run again.

    Returns ``None`` when ``job_id`` is unknown; a :class:`JobResult` with
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

    if job.params.get("run_once") and await _occurrence_completed(db, job):
        # A one-shot that already FINISHED: its retirement failed and a reaper put it
        # back to 'pending'. Running it again is the duplicate this refuses.
        deleted = await delete_finished_one_shot(
            db, job.job_id, handler=job.handler_name, outcome="run_now found it finished",
        )
        log.scheduler.warning(
            "[scheduler] run_now: rejected — one-shot already finished; %s instead of run",
            "retired" if deleted else "retirement failed again",
            extra={"_fields": {"job_id": job_id, "deleted": deleted}},
        )
        return _rejected(job_id, "one-shot already finished — retired, not run again")

    # In-flight CAS: only the dispatcher that flips pending→running may run. If
    # the poller already claimed it (status != 'pending'), we lose and reject —
    # no double dispatch. ``cursor.rowcount``, read directly off the
    # just-executed statement inside the SAME transaction, reports exactly how
    # many rows that UPDATE touched (1 = we won, 0 = lost / not pending) —
    # mirrors the poller's own CAS shape (``JobScheduler._run_job``).
    #
    # Spec 2.6/AD-24: the claim UPDATE and its ``job.started`` journal row
    # commit in ONE transaction — a manually-triggered run is covered
    # identically to the poller's own dispatch.
    async with db.transaction() as conn:
        cursor = await conn.execute(
            # Stamped by the claim itself — see the note at the poll dispatcher's CAS.
            "UPDATE jobs SET status = 'running', claimed_at = ? "
            "WHERE job_id = ? AND status = 'pending'",
            (datetime.now(UTC).isoformat(), job_id),
        )
        won = cursor.rowcount == 1
        if won:
            await journal_record(conn, JournalEvent(
                type="job.started",
                schema_version=1,
                actor_kind=ActorKind.AUTONOMOUS,
                actor_id="scheduler",
                target_kind=ActorKind.OWNER,
                target_id=job_id,
                outcome=Outcome.OK,
                record_ref=_job_record_ref(job_id),
                attrs=JobStartedAttrs(handler_name=job.handler_name[:_MAX_LABEL_LEN]),
            ))
    if not won:
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
        await _restore_after_run(db, job, tz=tz)
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

    duration_ms = (clock.monotonic() - t0) * 1000
    await _record_run(db, job, result, duration_ms)
    if job.params.get("run_once"):
        await settle(job, result, duration_ms)
    else:
        await _restore_after_run(db, job, tz=tz)
    await write_audit(db, "job_run_now", job_id, details={"success": result.success})
    log.scheduler.info(
        "[scheduler] run_now: exit",
        extra={"_fields": {"job_id": job_id, "success": result.success}},
    )
    return result


def _rejected(job_id: str, reason: str) -> JobResult:
    return JobResult(job_id=job_id, success=False, output=None, error=reason, duration_ms=0.0)


async def _occurrence_completed(db: DbPool, job: Job) -> bool:
    """True when ``job_runs`` already records this occurrence as completed.

    The same key and question ``JobScheduler._run_job``'s dedup asks, so ``run_now``
    and the poller cannot disagree about whether a one-shot has finished.
    """
    rows = await db.fetch_all(
        "SELECT 1 FROM job_runs WHERE idempotency_key = ? AND status = 'completed' LIMIT 1",
        (f"{job.idempotency_key}@{job.next_run_at}",),
    )
    return bool(rows)


async def _record_run(db: DbPool, job: Job, result: JobResult, duration_ms: float) -> None:
    """Write a ``job_runs`` row for this out-of-band run (mirrors the poller).

    Keys the row to the occurrence (``idempotency_key@next_run_at``) exactly as
    :meth:`JobScheduler._occurrence_key` does, so an out-of-band run cannot
    poison the dedup lookup for a future poll at a different scheduled instant.
    """
    now_iso = datetime.now(UTC).isoformat()
    run_id = str(uuid.uuid4())
    # A VETOED success (success=True, verified=False) is not a completed run.
    # Recorded as one, the next poll's dedup would retire a one-shot that never
    # really ran — no retry, no audit, no alert.
    trustworthy = is_trustworthy_success(result.success, result.verified)
    status = "completed" if trustworthy else "failed"
    occurrence_key = f"{job.idempotency_key}@{job.next_run_at}"
    # Spec 2.6/AD-24: the job_runs history row and (for a RECURRING job's
    # successful run only) ``job.finished`` commit in ONE transaction. A
    # ONE-SHOT's ``job.finished`` comes from its own retirement path
    # (``settle`` -> ``_mark_completed`` -> ``_retire_completed_one_shot``,
    # already wrapped there) — recording it here too would double-journal the
    # SAME transition.
    record_finished = trustworthy and not job.params.get("run_once")
    async with db.transaction() as conn:
        await conn.execute(
            "INSERT INTO job_runs (run_id, job_id, idempotency_key, status, duration_ms, ran_at) "
            "VALUES (?,?,?,?,?,?)",
            (run_id, job.job_id, occurrence_key, status, duration_ms, now_iso),
        )
        if record_finished:
            await journal_record(conn, JournalEvent(
                type="job.finished",
                schema_version=1,
                actor_kind=ActorKind.AUTONOMOUS,
                actor_id="scheduler",
                target_kind=ActorKind.OWNER,
                target_id=job.job_id,
                outcome=Outcome.OK,
                record_ref=_job_record_ref(job.job_id),
                attrs=JobFinishedAttrs(handler_name=job.handler_name[:_MAX_LABEL_LEN]),
            ))


async def _restore_after_run(db: DbPool, job: Job, *, tz: str = "UTC") -> None:
    """Return a job to ``pending`` after an out-of-band run the poller does not settle.

    RECURRING (after any run_now): its next slot, recomputed with ``tz`` (the user
    IANA timezone) so a ``daily@HH:MM`` job's next occurrence stays on its
    configured local time rather than silently re-arming in UTC.

    ONE-SHOT: reached only when it never ran (no handler registered) — a one-shot
    that ran is settled by the poller's disposition instead. It keeps its OWN slot:
    "manual" is not a cadence, and ``compute_next_run`` answers an unreadable one
    with +1 day, which is how run_now used to re-arm a one-shot a day out.
    """
    log.scheduler.debug(
        "[scheduler] run_now._restore_after_run: entry",
        extra={"_fields": {"job_id": job.job_id}},
    )
    if job.params.get("run_once"):
        await db.execute(
            "UPDATE jobs SET status = 'pending' WHERE job_id = ?", (job.job_id,),
        )
        log.scheduler.info(
            "[scheduler] run_now._restore_after_run: exit — one-shot did not run; "
            "returned to pending at its own slot",
            extra={"_fields": {"job_id": job.job_id, "next_run_at": job.next_run_at}},
        )
        return
    next_run = compute_next_run(job.schedule, tz=tz)
    await db.execute(
        "UPDATE jobs SET status = 'pending', last_run_at = ?, next_run_at = ? WHERE job_id = ?",
        (datetime.now(UTC).isoformat(), next_run, job.job_id),
    )
    log.scheduler.info(
        "[scheduler] run_now._restore_after_run: exit — recurring job re-armed",
        extra={"_fields": {"job_id": job.job_id, "next_run_at": next_run}},
    )
