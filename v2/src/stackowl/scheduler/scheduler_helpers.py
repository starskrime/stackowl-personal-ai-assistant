"""Helpers for :class:`JobScheduler` lifecycle operations (Story 7.1)."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from stackowl.db.pool import DbPool
from stackowl.infra.observability import log
from stackowl.scheduler.job import Job

_INSERT_AUDIT_SQL = (
    "INSERT INTO audit_log (audit_id, event_type, actor, target, timestamp, details) "
    "VALUES (?, ?, ?, ?, ?, ?)"
)

_INSERT_JOB_SQL = (
    "INSERT INTO jobs "
    "(job_id, handler_name, schedule, idempotency_key, last_run_at, next_run_at, "
    "status, retry_count, created_at, failure_count, last_error, enabled, "
    "replay_missed, primary_channel, params) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


async def write_audit(
    db: DbPool,
    event_type: str,
    target: str,
    actor: str = "user",
    details: dict[str, Any] | None = None,
) -> None:
    """Insert a row into ``audit_log`` for a scheduler lifecycle event."""
    log.scheduler.debug(
        "[scheduler] audit.write: entry",
        extra={"_fields": {"event_type": event_type, "target": target}},
    )
    payload = json.dumps(details or {}, separators=(",", ":"), sort_keys=True)
    try:
        await db.execute(
            _INSERT_AUDIT_SQL,
            (
                str(uuid.uuid4()),
                event_type,
                actor,
                target,
                datetime.now(UTC).isoformat(),
                payload,
            ),
        )
    except Exception as exc:  # B5 — never silent
        log.scheduler.warning(
            "[scheduler] audit.write: insert failed",
            exc_info=exc,
            extra={"_fields": {"event_type": event_type, "target": target}},
        )
        return
    log.scheduler.debug(
        "[scheduler] audit.write: exit",
        extra={"_fields": {"event_type": event_type, "target": target}},
    )


def compute_next_run(schedule: str) -> str:
    """Compute the next ISO-8601 UTC run time from a schedule expression."""
    log.scheduler.debug(
        "[scheduler] compute_next_run: entry",
        extra={"_fields": {"schedule": schedule}},
    )
    if schedule.startswith("daily@"):
        parts = schedule[len("daily@") :].split(":")
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
        now = datetime.now(UTC)
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate.isoformat()
    try:
        from croniter import croniter  # type: ignore[import-untyped]

        it = croniter(schedule, datetime.now(UTC))
        next_dt: datetime = it.get_next(datetime)
        return next_dt.isoformat()
    except Exception as exc:  # B5
        log.scheduler.warning(
            "[scheduler] compute_next_run: cron parse failed — defaulting to +1d",
            exc_info=exc,
            extra={"_fields": {"schedule": schedule}},
        )
        return (datetime.now(UTC) + timedelta(days=1)).isoformat()


def row_to_job(row: dict[str, Any]) -> Job:
    """Build a :class:`Job` from a raw ``jobs`` row dict (handles legacy columns)."""
    raw_params = row.get("params")
    if isinstance(raw_params, str) and raw_params:
        try:
            params_dict = json.loads(raw_params)
        except json.JSONDecodeError as exc:  # B5
            log.scheduler.warning(
                "[scheduler] row_to_job: invalid params JSON — using empty dict",
                exc_info=exc,
                extra={"_fields": {"job_id": row.get("job_id")}},
            )
            params_dict = {}
    else:
        params_dict = raw_params if isinstance(raw_params, dict) else {}
    return Job(
        job_id=row["job_id"],
        handler_name=row["handler_name"],
        schedule=row["schedule"],
        idempotency_key=row["idempotency_key"],
        last_run_at=row.get("last_run_at"),
        next_run_at=row["next_run_at"],
        status=row["status"],
        retry_count=int(row.get("retry_count", 0) or 0),
        failure_count=int(row.get("failure_count", 0) or 0),
        last_error=row.get("last_error"),
        enabled=bool(row.get("enabled", 1)),
        replay_missed=bool(row.get("replay_missed", 0)),
        primary_channel=row.get("primary_channel"),
        params=params_dict,
    )


async def insert_job(db: DbPool, job: Job) -> None:
    """Insert a new ``jobs`` row from a :class:`Job` instance."""
    log.scheduler.debug(
        "[scheduler] insert_job: entry",
        extra={"_fields": {"job_id": job.job_id, "handler": job.handler_name}},
    )
    now_iso = datetime.now(UTC).isoformat()
    await db.execute(
        _INSERT_JOB_SQL,
        (
            job.job_id,
            job.handler_name,
            job.schedule,
            job.idempotency_key,
            job.last_run_at,
            job.next_run_at,
            job.status,
            job.retry_count,
            now_iso,
            job.failure_count,
            job.last_error,
            1 if job.enabled else 0,
            1 if job.replay_missed else 0,
            job.primary_channel,
            json.dumps(job.params, separators=(",", ":"), sort_keys=True),
        ),
    )
    log.scheduler.info(
        "[scheduler] insert_job: exit",
        extra={"_fields": {"job_id": job.job_id, "handler": job.handler_name}},
    )
