"""JobScheduler — polls the jobs table every 30s, runs due jobs (FR139)."""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

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
from stackowl.scheduler.scheduler_mutations import _won_transition, run_now, update_job
from stackowl.supervisor.supervisor import SupervisedTask

if TYPE_CHECKING:  # pragma: no cover — typing-only import (no runtime cost / cycle)
    from stackowl.notifications.proactive_job import ProactiveJobDeliverer

_POLL_INTERVAL_SEC = 30.0
_MAX_RETRIES = 3
_RETRY_DELAY_MIN = 5
# A `defer_under_load` job overdue by MORE than this is run anyway, so heavy
# background work is never indefinitely starved by a stream of user turns.
_MAX_DEFER_SEC = 900.0


def _unify_scheduler_enabled() -> bool:
    """ADR-2 flag read (``unify_scheduler_recovery``). Fail-safe to True (the owner-approved
    default) on any config error — a flag read must never break the poll loop. Consulted ONLY
    on the failure path, so a healthy job never constructs Settings here."""
    try:
        from stackowl.config.settings import Settings

        return bool(Settings().unify_scheduler_recovery)
    except Exception:  # noqa: BLE001 — a flag read must never raise into the scheduler
        return True


class JobScheduler(SupervisedTask):
    """Polls SQLite jobs table and dispatches due handlers (ARCH-99)."""

    def __init__(
        self,
        *,
        db: DbPool,
        clock: Clock = WallClock(),
        handler_registry: HandlerRegistry | None = None,
        tz: str = "UTC",
        turn_registry: Any = None,
        max_defer_sec: float = _MAX_DEFER_SEC,
        job_deliverer: ProactiveJobDeliverer | None = None,
        recovery: Any = None,
    ) -> None:
        self._db = db
        self._clock = clock
        # ADR-2 — the one recovery authority. The retry-vs-terminal-fail DECISION for a
        # failed job delegates to its ``should_retry`` predicate (flag
        # ``unify_scheduler_recovery``) so one policy governs every subsystem's recovery.
        # Stateless; injectable for tests. Lazily constructed to avoid an import at module
        # load (the actuator lives in the pipeline layer).
        self._recovery = recovery
        self._registry = handler_registry or HandlerRegistry.instance()
        # F-61 — the SHARED cron-born delivery seam (the same one morning_brief /
        # check_in / goal_execution use). When wired, a job that exhausts its
        # retries routes a proactive operator alert to its OWN durable recipients
        # so an outage is not just a buried ERROR log line. None ⇒ no alert
        # (back-compat for tests / non-orchestrated construction); the lifecycle
        # write always completes regardless.
        self._job_deliverer = job_deliverer
        # Optional TurnRegistry (duck-typed: needs has_active_turns()). When wired,
        # heavy `defer_under_load` handlers yield to live user turns. None ⇒ no
        # deferral (back-compat for tests / non-orchestrated construction).
        self._turn_registry = turn_registry
        self._max_defer_sec = max_defer_sec
        # The user IANA tz (settings.system.timezone) — threaded into every
        # ``compute_next_run`` so a ``daily@HH:MM`` job re-arms at the right LOCAL
        # instant and shares the quiet-hours clock (F108). Defaults to UTC for
        # back-compat with non-orchestrated construction (tests, tools).
        self._tz = tz

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
        # STEER-5/F113 — a job is due when EITHER its canonical recurring slot
        # (next_run_at) OR its separate retry slot (retry_at, when set) is reached.
        # retry_at is NULL for a healthy job, so this is byte-equivalent to the old
        # next_run_at-only select in the steady state.
        rows = await self._db.fetch_all(
            "SELECT * FROM jobs WHERE status = 'pending' AND enabled = 1 "
            "AND (next_run_at <= ? OR (retry_at IS NOT NULL AND retry_at <= ?))",
            (now_iso, now_iso),
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

    def _should_defer_under_load(self, job: Job) -> bool:
        """True when a heavy job should yield to a live user turn (and isn't yet
        overdue past the starvation cap). Pure read — leaves the job pending so
        the next poll retries it once the box is idle."""
        if self._turn_registry is None:
            return False
        handler = self._registry.get(job.handler_name)
        if handler is None or not getattr(handler, "defer_under_load", False):
            return False
        if not self._turn_registry.has_active_turns():
            return False
        # Starvation guard: once overdue beyond the cap, run anyway.
        try:
            due = datetime.fromisoformat(job.next_run_at)
            overdue_s = (datetime.now(UTC) - due).total_seconds()
        except (ValueError, TypeError):
            overdue_s = 0.0
        return overdue_s < self._max_defer_sec

    async def _run_job(self, job: Job) -> None:
        if self._should_defer_under_load(job):
            log.heartbeat.info(
                "[scheduler] %s: deferred — user turn active (heavy job yields)",
                job.job_id,
                extra={"_fields": {"job_id": job.job_id, "handler": job.handler_name}},
            )
            return  # left pending; the next idle poll will dispatch it
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
            # F-63 — the normal single-dispatch path advances the cadence slot in
            # ``_mark_completed``; this dedup branch previously returned WITHOUT
            # advancing ``next_run_at``. A recurring job whose current occurrence
            # is already recorded (a lost-race / out-of-band completion that left
            # the row at its past instant) then stayed ``pending`` at that PAST
            # instant and idempotent-skipped every subsequent poll forever — never
            # verifying its NEXT occurrence was scheduled. Advance it to the next
            # future slot so the schedule keeps progressing.
            await self._advance_past_serviced_occurrence(job)
            return

        # F103: claim the occurrence with the SAME compare-and-swap run_now uses,
        # so a concurrent poll tick and run_now (or two pollers) can never both
        # dispatch this job. A guarded ``pending -> running`` UPDATE plus
        # ``_won_transition`` (which reads ``changes()`` on the pool's single
        # serialized connection) reports whether THIS dispatcher won. If we lose
        # (another dispatcher already flipped the row), bail without running.
        # COUPLING: ``_won_transition``'s correctness DEPENDS on DbPool using one
        # serialized connection (``SELECT changes()`` reflects the immediately
        # preceding UPDATE on THAT connection). A future multi-connection pool
        # would silently corrupt this CAS — keep the pool single-connection.
        await self._db.execute(
            "UPDATE jobs SET status = 'running' WHERE job_id = ? AND status = 'pending'",
            (job.job_id,),
        )
        if not await _won_transition(self._db):
            log.heartbeat.info(
                "[scheduler] %s: lost dispatch claim — another worker is running it",
                job.job_id,
                extra={"_fields": {"job_id": job.job_id}},
            )
            return
        log.heartbeat.info(
            "[scheduler] %s: entry — running handler %s",
            job.job_id,
            job.handler_name,
            extra={"_fields": {"job_id": job.job_id, "handler": job.handler_name}},
        )
        t0 = time.monotonic()
        handler = self._registry.get(job.handler_name)
        if handler is None:
            # F-62 — the handler is not registered AT THIS TICK. That is a wiring /
            # registration-ordering condition (conditionally-registered handlers, or
            # registration sequenced after the first poll), NOT a handler failure.
            # Marking it terminally `failed` here made the job unreachable FOREVER —
            # even once the handler later registers. Instead, release the dispatch
            # claim (back to `pending`) and warn, leaving the row exactly as-due so a
            # subsequent poll recovers it the moment the handler appears. Terminal
            # `failed` is reserved for handler-RAISED errors past max-retries.
            # retry_count is deliberately untouched — a registration gap must never
            # consume the job's genuine handler-failure retry budget.
            await self._db.execute(
                "UPDATE jobs SET status = 'pending' WHERE job_id = ?",
                (job.job_id,),
            )
            log.heartbeat.warning(
                "[scheduler] %s: handler not registered yet — left pending for "
                "later recovery (NOT marked failed)",
                job.job_id,
                extra={"_fields": {"job_id": job.job_id, "handler": job.handler_name}},
            )
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
            if new_retries < _MAX_RETRIES and self._may_retry(result):
                # STEER-5/F113 — schedule the retry on the SEPARATE retry_at slot;
                # NEVER touch next_run_at (the canonical recurring cadence). A
                # daily@08:00 job that fails retries at ~08:05 via retry_at while
                # its 08:00-tomorrow cadence slot stays intact.
                retry_at = (datetime.now(UTC) + timedelta(minutes=_RETRY_DELAY_MIN)).isoformat()
                await self._db.execute(
                    "UPDATE jobs SET status = 'pending', retry_count = ?, retry_at = ? WHERE job_id = ?",
                    (new_retries, retry_at, job.job_id),
                )
            else:
                await self._mark_failed(job, last_error=result.error)

    def _may_retry(self, result: JobResult) -> bool:
        """Whether a failed job may be retried — the ONE recovery authority decides (ADR-2).

        When ``unify_scheduler_recovery`` is on (default) the retry-vs-terminal-fail decision
        is delegated to :meth:`RecoveryActuator.should_retry` over a typed ``Failure`` instead
        of the inline budget guard. A scheduled job failure is non-consequential and
        transient-by-policy (the scheduler's job is operational resilience), so the authority
        returns True and the outcome is byte-identical to the inline ``retry_count <
        _MAX_RETRIES`` gate — but the policy now lives in ONE place. Flag off ⇒ the inline
        budget gate decides alone (the actuator is not consulted), byte-identical to pre-ADR.
        A flag-read error fails safe to the unified path (the owner-approved default)."""
        if not _unify_scheduler_enabled():
            return True
        from stackowl.pipeline.recovery_actuator import Failure, RecoveryActuator

        if self._recovery is None:
            self._recovery = RecoveryActuator()
        failure = Failure(
            name=self._job_handler_name_for_failure(result),
            kind="scheduled_job",
            transient=True,
            consequential=False,
            error=result.error,
        )
        return bool(self._recovery.should_retry(failure))

    @staticmethod
    def _job_handler_name_for_failure(result: JobResult) -> str:
        """A stable label for the failure ledger — the job id (handler name is not on
        JobResult). Kept tiny so the Failure construction stays a pure data shape."""
        return result.job_id

    async def _advance_past_serviced_occurrence(self, job: Job) -> None:
        """Re-arm a recurring job past an already-serviced occurrence (F-63).

        Called from the idempotent-skip branch. Guards a livelock: a recurring
        job left ``pending`` at a PAST ``next_run_at`` with a recorded completion
        for that occurrence would be re-selected and re-skipped on every poll,
        never advancing. Only acts when the job is RECURRING and its
        ``next_run_at`` is at/behind now (an unparseable value is treated as stuck
        and repaired); a healthy FUTURE slot is left untouched, and a ONE-SHOT is
        never re-armed (a completed one-shot must not fire again). Writes no
        ``job_runs`` row — it only moves the slot, reusing the same
        ``compute_next_run`` the normal completion path uses.
        """
        if not self._is_recurring(job):
            return  # one-shot: completed and done — never re-arm to fire again
        try:
            due = datetime.fromisoformat(job.next_run_at)
            stuck = due <= datetime.now(UTC)
        except (ValueError, TypeError) as exc:  # B5 — never silent; repair the row
            log.heartbeat.warning(
                "[scheduler] %s: idempotent-skip — unparseable next_run_at, will re-arm",
                job.job_id,
                exc_info=exc,
                extra={"_fields": {"job_id": job.job_id, "next_run_at": job.next_run_at}},
            )
            stuck = True
        if not stuck:
            return  # healthy future slot — leave it
        next_run = compute_next_run(job.schedule, tz=self._tz)
        await self._db.execute(
            "UPDATE jobs SET next_run_at = ? WHERE job_id = ?",
            (next_run, job.job_id),
        )
        log.heartbeat.info(
            "[scheduler] %s: idempotent-skip — recurring job advanced to next slot",
            job.job_id,
            extra={"_fields": {"job_id": job.job_id, "next_run": next_run}},
        )

    async def _mark_completed(self, job: Job, result: JobResult, duration_ms: float) -> None:
        now_iso = datetime.now(UTC).isoformat()
        next_run = compute_next_run(job.schedule, tz=self._tz)
        run_id = str(uuid.uuid4())
        # STEER-5/F113 — on success, recompute the canonical cadence AND clear the
        # transient retry state (retry_count=0, retry_at=NULL) so a previously
        # flaky job returns to a clean steady state on its real schedule. Also reset
        # failure_count: it counts CONSECUTIVE failed runs (the circuit-breaker
        # input, S11c) — one success closes the breaker.
        await self._db.execute(
            "UPDATE jobs SET status = 'pending', last_run_at = ?, next_run_at = ?, "
            "retry_count = 0, retry_at = NULL, failure_count = 0 WHERE job_id = ?",
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

    @staticmethod
    def _is_recurring(job: Job) -> bool:
        """True when this job fires on a repeating cadence (must survive failure).

        F-60: a RECURRING job (morning_brief, check_in, every-N sweeps, daily@,
        cron) must NEVER go terminal ``failed`` after a burst of transient
        failures — its next occurrence has to fire. A ONE-SHOT job (``run_once``,
        which deletes its own row on success and would otherwise linger as a dead
        ``failed`` row) stays terminal.

        Detection reuses the SAME explicit marker the rest of the scheduler keys
        on for the run-once/recurring fork (``goal_execution._delete_job``,
        ``scheduler_mutations._restore_after_run``): ``params['run_once']``. This
        is schedule-DSL-agnostic — any seeded standing job (none of which set
        ``run_once``) is recurring, which is exactly the set that must self-heal.
        """
        return not bool(job.params.get("run_once"))

    async def _mark_failed(self, job: Job, last_error: str | None = None) -> None:
        """Terminate a one-shot job, or RE-ARM a recurring one (F-60).

        For a recurring job, exhausting the within-occurrence retries must not
        kill the schedule: recompute the canonical cadence slot (the same
        ``compute_next_run`` used on normal completion), clear the transient
        retry state (``retry_count=0``, ``retry_at=NULL``), and return the row to
        ``pending`` so the NEXT occurrence fires. Terminal ``failed`` is reserved
        for one-shots. Either transition writes an audit row (every other
        lifecycle transition does) so a re-arm-after-failure is never silent.
        """
        if not self._is_recurring(job):
            await self._db.execute(
                "UPDATE jobs SET status = 'failed', last_error = ? WHERE job_id = ?",
                (last_error, job.job_id),
            )
            log.heartbeat.error(
                "[scheduler] %s: max retries reached — one-shot marked permanently failed",
                job.job_id,
                extra={"_fields": {"job_id": job.job_id, "retries": job.retry_count + 1}},
            )
            await write_audit(
                self._db,
                "job_failed_terminal",
                job.job_id,
                actor="scheduler",
                details={"handler": job.handler_name, "last_error": last_error},
            )
            await self._notify_failure(job, last_error, terminal=True)
            return

        # CIRCUIT-BREAKER (S11c) — a scheduled OWL's job that fails this many
        # consecutive runs is PAUSED (not re-armed) and the user is alerted ONCE,
        # so a broken owl never fires forever. Scoped to owl-lifecycle jobs by
        # provenance marker; every other recurring job keeps its re-arm behavior.
        from stackowl.owls.owl_schedule_guards import (
            MAX_CONSECUTIVE_FAILURES,
            OWL_LIFECYCLE_SOURCE,
        )

        new_failure_count = job.failure_count + 1
        is_owl_job = job.params.get("source") == OWL_LIFECYCLE_SOURCE
        if is_owl_job and new_failure_count >= MAX_CONSECUTIVE_FAILURES:
            await self._db.execute(
                "UPDATE jobs SET status = 'failed', enabled = 0, "
                "retry_count = 0, retry_at = NULL, failure_count = ?, last_error = ? "
                "WHERE job_id = ?",
                (new_failure_count, last_error, job.job_id),
            )
            log.heartbeat.error(
                "[scheduler] %s: scheduled owl job circuit-broken — paused after "
                "%d consecutive failures",
                job.job_id,
                new_failure_count,
                extra={"_fields": {
                    "job_id": job.job_id,
                    "owner": job.params.get("owner"),
                    "failures": new_failure_count,
                }},
            )
            await write_audit(
                self._db,
                "owl_job_circuit_broken",
                job.job_id,
                actor="scheduler",
                details={"owner": job.params.get("owner"), "failures": new_failure_count,
                         "last_error": last_error},
            )
            await self._notify_failure(job, last_error, terminal=True)
            return

        # Recurring job: re-arm onto the next cadence slot instead of dying.
        now_iso = datetime.now(UTC).isoformat()
        next_run = compute_next_run(job.schedule, tz=self._tz)
        await self._db.execute(
            "UPDATE jobs SET status = 'pending', last_run_at = ?, next_run_at = ?, "
            "retry_count = 0, retry_at = NULL, failure_count = ?, "
            "last_error = ? WHERE job_id = ?",
            (now_iso, next_run, new_failure_count, last_error, job.job_id),
        )
        log.heartbeat.warning(
            "[scheduler] %s: max retries reached — recurring job RE-ARMED to next slot",
            job.job_id,
            extra={
                "_fields": {
                    "job_id": job.job_id,
                    "handler": job.handler_name,
                    "retries": job.retry_count + 1,
                    "next_run": next_run,
                }
            },
        )
        # The audit row above is the durable, operator-visible record of the
        # re-arm. F-61 — a recurring job exhausting its retries is a genuine outage,
        # so beyond the durable-but-silent audit row we ALSO push a proactive
        # operator alert through the shared cron-born delivery seam (when wired).
        await write_audit(
            self._db,
            "job_rearmed_after_failure",
            job.job_id,
            actor="scheduler",
            details={
                "handler": job.handler_name,
                "next_run_at": next_run,
                "last_error": last_error,
            },
        )
        # An owl-lifecycle job stays SILENT on each intermediate re-arm so its only
        # alert is the single circuit-break notification at the failure threshold
        # above (S11c: "pause + ONE notification"). Every other recurring job keeps
        # the F-61 per-re-arm operator alert.
        if not is_owl_job:
            await self._notify_failure(job, last_error, terminal=False)

    async def _notify_failure(
        self, job: Job, last_error: str | None, *, terminal: bool
    ) -> None:
        """Route an operator alert for a retry-exhausted job (F-61).

        A job that exhausts its retries — whether it dies (one-shot ``terminal``)
        or re-arms onto its next slot (recurring) — is an outage whose only prior
        signal was a buried ERROR log line. This pushes a proactive notification
        through the SAME delivery seam (:class:`ProactiveJobDeliverer`) that
        morning_brief / check_in / goal_execution use, addressed from the job's
        OWN durable recipients (``target_channels`` / ``target_addresses``).

        Best-effort and HONEST: with no deliverer wired (non-orchestrated
        construction, or no proactive channel configured) nothing is sent; a job
        with no durable recipient is reported ``undeliverable`` by the seam (never
        a fake "notified"); and any send error is logged but NEVER allowed to abort
        the durable lifecycle write that already happened.
        """
        if self._job_deliverer is None:
            log.heartbeat.debug(
                "[scheduler] %s: no deliverer wired — failure alert skipped",
                job.job_id,
                extra={"_fields": {"job_id": job.job_id}},
            )
            return
        disposition = (
            "permanently failed"
            if terminal
            else "is failing repeatedly (re-armed to its next slot)"
        )
        message = f"Scheduled job '{job.handler_name}' {disposition} after exhausting retries."
        if last_error:
            message += f" Last error: {last_error}"
        try:
            outcome = await self._job_deliverer.deliver_for_job(
                job,
                message=message,
                category="job_failed",
                urgency="high",
            )
            log.heartbeat.info(
                "[scheduler] %s: failure alert routed",
                job.job_id,
                extra={
                    "_fields": {
                        "job_id": job.job_id,
                        "terminal": terminal,
                        "rollup": getattr(outcome, "rollup", None),
                    }
                },
            )
        except Exception as exc:  # B5 — a notify failure must not break the lifecycle
            log.heartbeat.error(
                "[scheduler] %s: failure alert delivery raised",
                job.job_id,
                exc_info=exc,
                extra={"_fields": {"job_id": job.job_id}},
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
        next_run = compute_next_run(rows[0]["schedule"], tz=self._tz)
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

    async def snooze(self, job_id: str, until_iso: str) -> None:
        """Snooze a job until ``until_iso``, then let it auto-resume its cadence.

        Unlike :meth:`pause` (which disables the row), snooze keeps ``enabled=1``
        and simply pushes ``next_run_at`` into the future: the poller selects
        ``pending AND enabled=1 AND next_run_at <= now``, so the job is silent until
        ``until_iso`` and then fires + re-arms on its normal schedule — no manual
        resume needed. Survives reconcile (no manifest change → owned-row no-op)."""
        log.scheduler.debug(
            "[scheduler] snooze: entry",
            extra={"_fields": {"job_id": job_id, "until": until_iso}},
        )
        await self._db.execute(
            "UPDATE jobs SET status = 'pending', enabled = 1, next_run_at = ? WHERE job_id = ?",
            (until_iso, job_id),
        )
        await write_audit(self._db, "job_snoozed", job_id, details={"until": until_iso})
        log.scheduler.info(
            "[scheduler] snooze: exit",
            extra={"_fields": {"job_id": job_id, "until": until_iso}},
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
                next_run = compute_next_run(job.schedule, tz=self._tz)
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
        target_channels: list[str] | None = None,
        target_addresses: dict[str, str | int] | None = None,
    ) -> Job:
        """Insert and return a new ``jobs`` row.

        ``target_channels`` / ``target_addresses`` stamp the DURABLE delivery
        recipient onto the job row at creation (C1/F104) so a cron-born poll (no
        session, no TraceContext) can address its send from durable state. Both
        default to empty — every existing caller stays byte-identical.
        """
        log.scheduler.debug(
            "[scheduler] create_job: entry",
            extra={"_fields": {"handler": handler_name, "schedule": schedule}},
        )
        job_id = f"{handler_name}-{uuid.uuid4().hex[:8]}"
        next_run = compute_next_run(schedule, tz=self._tz)
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
            target_channels=list(target_channels or []),
            target_addresses=dict(target_addresses or {}),
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
