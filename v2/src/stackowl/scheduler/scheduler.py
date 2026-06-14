"""JobScheduler — polls the jobs table every 30s, runs due jobs (FR139)."""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime, timedelta

from stackowl.config.test_mode import TestModeGuard
from stackowl.db.pool import DbPool
from stackowl.infra.clock import Clock, WallClock
from stackowl.infra.observability import log
from stackowl.scheduler.base import HandlerRegistry
from stackowl.scheduler.job import Job, JobResult
from stackowl.scheduler.scheduler_helpers import (
    compute_next_run,
    insert_job,
    reap_stale_running,
    row_to_job,
    write_audit,
)
from stackowl.scheduler.scheduler_mutations import run_now, update_job
from stackowl.supervisor.supervisor import SupervisedTask

_POLL_INTERVAL_SEC = 30.0
_MAX_RETRIES = 3
_RETRY_DELAY_MIN = 5


class JobScheduler(SupervisedTask):
    """Polls SQLite jobs table and dispatches due handlers (ARCH-99)."""

    def __init__(
        self,
        *,
        db: DbPool,
        clock: Clock = WallClock(),
        handler_registry: HandlerRegistry | None = None,
    ) -> None:
        self._db = db
        self._clock = clock
        self._registry = handler_registry or HandlerRegistry.instance()

    @property
    def task_id(self) -> str:
        return "job_scheduler"

    async def run(self) -> None:
        log.heartbeat.info("[scheduler] run: starting poll loop")
        while True:
            t0 = self._clock.monotonic()
            await self._poll()
            elapsed = (self._clock.monotonic() - t0) * 1000
            log.heartbeat.debug(
                "[scheduler] run: poll cycle complete",
                extra={"_fields": {"duration_ms": elapsed}},
            )
            await self._clock.async_sleep(_POLL_INTERVAL_SEC)

    async def _poll(self) -> None:
        TestModeGuard.assert_not_test_mode("scheduler.execute")
        now_iso = datetime.now(UTC).isoformat()
        rows = await self._db.fetch_all(
            "SELECT * FROM jobs WHERE next_run_at <= ? AND status = 'pending' AND enabled = 1",
            (now_iso,),
        )
        for row in rows:
            await self._run_job(row_to_job(row))

    def _occurrence_key(self, job: Job) -> str:
        """Dedup key scoped to the SCHEDULED INSTANT being serviced.

        The static ``idempotency_key`` means "run once ever" — wrong for a
        recurring job. Suffixing the occurrence's ``next_run_at`` makes the same
        scheduled instant idempotent while each new instant is a fresh run.
        """
        return f"{job.idempotency_key}@{job.next_run_at}"

    async def _run_job(self, job: Job) -> None:
        occurrence_key = self._occurrence_key(job)
        already = await self._db.fetch_all(
            "SELECT status FROM job_runs WHERE idempotency_key = ? AND status = 'completed'",
            (occurrence_key,),
        )
        if already:
            log.heartbeat.info(
                "[scheduler] %s: idempotent skip",
                job.job_id,
                extra={"_fields": {"job_id": job.job_id, "occurrence_key": occurrence_key}},
            )
            return

        await self._db.execute(
            "UPDATE jobs SET status = 'running' WHERE job_id = ?",
            (job.job_id,),
        )
        log.heartbeat.info(
            "[scheduler] %s: entry — running handler %s",
            job.job_id,
            job.handler_name,
            extra={"_fields": {"job_id": job.job_id, "handler": job.handler_name}},
        )
        t0 = time.monotonic()
        handler = self._registry.get(job.handler_name)
        if handler is None:
            log.heartbeat.error(
                "[scheduler] %s: unknown handler — marking failed",
                job.job_id,
                extra={"_fields": {"handler": job.handler_name}},
            )
            await self._mark_failed(job)
            return

        try:
            result = await handler.execute(job)
        except Exception as exc:
            duration_ms = (time.monotonic() - t0) * 1000
            log.heartbeat.error(
                "[scheduler] %s: handler raised",
                job.job_id,
                exc_info=exc,
                extra={"_fields": {"job_id": job.job_id, "duration_ms": duration_ms}},
            )
            result = JobResult(job_id=job.job_id, success=False, output=None, error=str(exc), duration_ms=duration_ms)

        duration_ms = (time.monotonic() - t0) * 1000
        if result.success:
            await self._mark_completed(job, result, duration_ms)
        else:
            new_retries = job.retry_count + 1
            if new_retries >= _MAX_RETRIES:
                log.heartbeat.error(
                    "[scheduler] %s: max retries reached — marking permanently failed",
                    job.job_id,
                    extra={"_fields": {"job_id": job.job_id, "retries": new_retries}},
                )
                await self._mark_failed(job)
            else:
                retry_at = (datetime.now(UTC) + timedelta(minutes=_RETRY_DELAY_MIN)).isoformat()
                await self._db.execute(
                    "UPDATE jobs SET status = 'pending', retry_count = ?, next_run_at = ? WHERE job_id = ?",
                    (new_retries, retry_at, job.job_id),
                )

    async def _mark_completed(self, job: Job, result: JobResult, duration_ms: float) -> None:
        now_iso = datetime.now(UTC).isoformat()
        next_run = compute_next_run(job.schedule)
        run_id = str(uuid.uuid4())
        await self._db.execute(
            "UPDATE jobs SET status = 'pending', last_run_at = ?, next_run_at = ? WHERE job_id = ?",
            (now_iso, next_run, job.job_id),
        )
        await self._db.execute(
            "INSERT INTO job_runs (run_id, job_id, idempotency_key, status, duration_ms, ran_at) VALUES (?,?,?,?,?,?)",
            (run_id, job.job_id, self._occurrence_key(job), "completed", duration_ms, now_iso),
        )
        log.heartbeat.info(
            "[scheduler] %s: exit — completed",
            job.job_id,
            extra={"_fields": {"job_id": job.job_id, "duration_ms": duration_ms, "next_run": next_run}},
        )

    async def _mark_failed(self, job: Job) -> None:
        await self._db.execute(
            "UPDATE jobs SET status = 'failed' WHERE job_id = ?",
            (job.job_id,),
        )

    async def pause(self, job_id: str) -> None:
        """Pause a job — sets status='failed', enabled=0. Idempotent."""
        log.scheduler.debug("[scheduler] pause: entry", extra={"_fields": {"job_id": job_id}})
        await self._db.execute(
            "UPDATE jobs SET status = 'failed', enabled = 0 WHERE job_id = ?",
            (job_id,),
        )
        await write_audit(self._db, "job_paused", job_id)
        log.scheduler.info("[scheduler] pause: exit", extra={"_fields": {"job_id": job_id}})

    async def resume(self, job_id: str) -> None:
        """Resume a job — clears failure_count/last_error and recomputes next_run_at."""
        log.scheduler.debug("[scheduler] resume: entry", extra={"_fields": {"job_id": job_id}})
        rows = await self._db.fetch_all("SELECT schedule FROM jobs WHERE job_id = ?", (job_id,))
        if not rows:
            log.scheduler.warning("[scheduler] resume: job not found", extra={"_fields": {"job_id": job_id}})
            return
        next_run = compute_next_run(rows[0]["schedule"])
        await self._db.execute(
            "UPDATE jobs SET status = 'pending', enabled = 1, failure_count = 0, "
            "last_error = NULL, next_run_at = ? WHERE job_id = ?",
            (next_run, job_id),
        )
        await write_audit(self._db, "job_resumed", job_id, details={"next_run_at": next_run})
        log.scheduler.info(
            "[scheduler] resume: exit",
            extra={"_fields": {"job_id": job_id, "next_run_at": next_run}},
        )

    async def stop_job(self, job_id: str) -> None:
        """Permanently remove a job from the schedule."""
        log.scheduler.debug("[scheduler] stop_job: entry", extra={"_fields": {"job_id": job_id}})
        await self._db.execute("DELETE FROM jobs WHERE job_id = ?", (job_id,))
        await write_audit(self._db, "job_stopped", job_id)
        log.scheduler.info("[scheduler] stop_job: exit", extra={"_fields": {"job_id": job_id}})

    async def recover(self, replay_window_hours: int = 24) -> int:
        """Re-arm overdue pending jobs after a restart.

        ``replay_missed`` jobs missed inside ``replay_window_hours`` are
        dispatched once; the rest just advance ``next_run_at``. Returns the
        count replayed.
        """
        log.scheduler.info(
            "[scheduler] recover: entry",
            extra={"_fields": {"window_hours": replay_window_hours}},
        )
        await reap_stale_running(self._db)
        now = datetime.now(UTC)
        sql = "SELECT * FROM jobs WHERE status = 'pending' AND next_run_at <= ?"
        rows = await self._db.fetch_all(sql, (now.isoformat(),))
        replayed = 0
        for row in rows:
            job = row_to_job(row)
            try:
                missed_at = datetime.fromisoformat(job.next_run_at)
            except ValueError as exc:  # B5
                log.scheduler.warning(
                    "[scheduler] recover: invalid next_run_at — skipping",
                    exc_info=exc,
                    extra={"_fields": {"job_id": job.job_id}},
                )
                continue
            inside_window = (now - missed_at) <= timedelta(hours=replay_window_hours)
            if job.replay_missed and inside_window:
                log.scheduler.info(
                    "[scheduler] recover: replaying missed job",
                    extra={"_fields": {"job_id": job.job_id}},
                )
                await self._run_job(job)
                replayed += 1
            else:
                next_run = compute_next_run(job.schedule)
                await self._db.execute(
                    "UPDATE jobs SET next_run_at = ? WHERE job_id = ?",
                    (next_run, job.job_id),
                )
        log.scheduler.info(
            "[scheduler] recover: exit",
            extra={"_fields": {"due_jobs": len(rows), "replayed": replayed}},
        )
        return replayed

    async def create_job(
        self,
        *,
        handler_name: str,
        schedule: str,
        idempotency_key: str | None = None,
        params: dict[str, object] | None = None,
        replay_missed: bool = False,
        primary_channel: str | None = None,
    ) -> Job:
        """Insert and return a new ``jobs`` row."""
        log.scheduler.debug(
            "[scheduler] create_job: entry",
            extra={"_fields": {"handler": handler_name, "schedule": schedule}},
        )
        job_id = f"{handler_name}-{uuid.uuid4().hex[:8]}"
        next_run = compute_next_run(schedule)
        job = Job(
            job_id=job_id,
            handler_name=handler_name,
            schedule=schedule,
            idempotency_key=idempotency_key or f"{handler_name}:{job_id}",
            last_run_at=None,
            next_run_at=next_run,
            status="pending",
            params=dict(params or {}),
            replay_missed=replay_missed,
            primary_channel=primary_channel,
        )
        await insert_job(self._db, job)
        log.scheduler.info(
            "[scheduler] create_job: exit",
            extra={"_fields": {"job_id": job_id, "next_run_at": next_run}},
        )
        return job

    async def list_jobs(self) -> list[Job]:
        """Return every row in the ``jobs`` table as :class:`Job` objects."""
        log.scheduler.debug("[scheduler] list_jobs: entry")
        rows = await self._db.fetch_all("SELECT * FROM jobs ORDER BY job_id")
        jobs = [row_to_job(row) for row in rows]
        log.scheduler.debug("[scheduler] list_jobs: exit", extra={"_fields": {"count": len(jobs)}})
        return jobs

    async def update_job(
        self,
        job_id: str,
        *,
        schedule: str | None = None,
        goal: str | None = None,
        params: dict[str, object] | None = None,
    ) -> Job | None:
        """Update a job in place — thin delegate to ``scheduler_mutations`` (B2)."""
        return await update_job(self._db, job_id, schedule=schedule, goal=goal, params=params)

    async def run_now(self, job_id: str) -> JobResult | None:
        """Run one job out of band — thin delegate; mirrors the poller's CAS (B2)."""
        return await run_now(self._db, self._clock, self._registry, job_id)
