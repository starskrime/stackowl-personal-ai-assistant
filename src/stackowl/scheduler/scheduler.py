"""JobScheduler — polls the jobs table every 30s, runs due jobs (FR139)."""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from stackowl.config.test_mode import TestModeGuard
from stackowl.db.pool import DbPool
from stackowl.infra import retry_ledger
from stackowl.infra.clock import Clock, WallClock
from stackowl.infra.observability import log
from stackowl.infra.trace import TraceContext

# A leaf module by design — it imports nothing of the platform but the logger —
# so the scheduler can ask it at module level without dragging in the durable
# loop's database chain. ONE home for 'what kind of failure is this'.
from stackowl.pipeline.durable.failure_class import classify_failure, is_permanent
from stackowl.scheduler.base import HandlerRegistry
from stackowl.scheduler.job import Job, JobResult
from stackowl.scheduler.scheduler_helpers import (
    _STALE_RUNNING_AFTER_SEC,
    compute_next_run,
    insert_job,
    reap_stale_running,
    reap_timed_out_running,
    row_to_job,
    write_audit,
)
from stackowl.scheduler.scheduler_mutations import _won_transition, run_now, update_job
from stackowl.supervisor.supervisor import SupervisedTask
from stackowl.tools.verification import is_trustworthy_success

if TYPE_CHECKING:  # pragma: no cover — typing-only import (no runtime cost / cycle)
    from stackowl.notifications.proactive_job import ProactiveJobDeliverer

_POLL_INTERVAL_SEC = 30.0
_MAX_RETRIES = 3
_RETRY_DELAY_MIN = 5
# A `defer_under_load` job overdue by MORE than this is run anyway, so heavy
# background work is never indefinitely starved by a stream of user turns.
_MAX_DEFER_SEC = 900.0
# _run_job's poll iteration is sequential (`for row in rows: await
# self._run_job(...)`) — a handler that never returns (a hung network call, a
# deadlock) freezes EVERY subsequent job's dispatch forever, not just its own
# (live incident, 2026-07: the `dream_worker` job — since DELETED — hung, and no
# reminder/canary/incident job fired again for 45+ minutes until process
# restart). 20 minutes comfortably clears the slowest run ever observed
# (~16 min, that same job) while still
# bounding the worst case. A timeout routes into the SAME retry/re-arm path an
# ordinary handler exception already takes — recurring jobs self-heal (F-60),
# one-shots retry up to _MAX_RETRIES.
#: Where a ONE-SHOT job goes when it succeeds. Already in the ``jobs`` CHECK
#: constraint ('pending','running','completed','failed') and, until 2026-08-31,
#: written by nothing — the live table held 126 pending, 1 failed and 0 completed
#: while a method named ``_mark_completed`` re-armed every one-shot to 'pending'.
ONE_SHOT_TERMINAL_STATUS = "completed"

_HANDLER_TIMEOUT_SEC = 1200.0
# ESC-53. Backoff ladder for RE-ARMING a one-shot that failed transiently. Bakir,
# 2026-08-24: "Re-arm one-shots too." Backoff is what makes never-give-up safe —
# it bounds the RATE rather than the number of attempts, exactly as a recurring
# job's cadence does. The last entry is the steady state: a one-shot blocked on
# something genuinely long-lived retries hourly forever rather than spinning or
# dying. Measured cause: 54 rollover summaries lost across 16 days to provider
# circuit-opens lasting minutes.
_ONE_SHOT_REARM_BACKOFF_SEC: tuple[int, ...] = (300, 900, 1800, 3600)


def _unify_scheduler_enabled() -> bool:
    """ADR-2 flag read (``unify_scheduler_recovery``). Fail-safe to True (the owner-approved
    default) on any config error — a flag read must never break the poll loop. Consulted ONLY
    on the failure path, so a healthy job never constructs Settings here."""
    try:
        from stackowl.config.settings import Settings

        return bool(Settings().unify_scheduler_recovery)
    except Exception:  # noqa: BLE001 — a flag read must never raise into the scheduler
        return True


def _bind_job_trace(job: Any) -> Any:
    """Give this job RUN its own trace, so the model calls it makes are attributable.

    MEASURED 2026-08-29: 67,383 of 123,648 recorded LLM calls (54.5%) carried a
    BLANK trace_id — and a blank session_key and conversation_id with it — totalling
    127,088,340 input tokens (19.7% all-time; 4.0% over the last 24h, so this is
    attribution work rather than a spend emergency). They are the background
    handlers — critic, reflection_writer, learning, entity_extractor,
    rollover_summary — which call ``provider.complete`` directly and never build a
    PipelineState. ``_record_cost`` reads trace_id off TraceContext, so with nothing
    bound a fifth of all recorded spend was attributable to nothing at all.

    ``TraceContext.start`` was built for exactly this and never called from here —
    its own docstring says "useful for background jobs/scheduler handlers that start
    their own root trace".

    A FRESH TRACE PER RUN, not the job_id. ``job_id`` is stable for the life of a
    recurring job, so using it would fold that job's entire history into one
    accumulating per-trace total — and the per-turn token ceiling reads exactly that
    total, so a daily job would eventually breach a cap it never earned. The job's
    identity rides ``session_key`` instead, where it groups without accumulating.

    ``conversation_id`` IS DELIBERATELY LEFT NONE. From ``trace.py``: "None is a real
    answer: background work that never passed through ingress has a lane but no
    incarnation, and inventing one would attribute its cost to a conversation that
    never happened." A job gets a trace and a lane, never a conversation.

    A CHANNEL IS BOUND TOO, and its absence was a live defect. Measured
    2026-08-31: five ``tool_build.execute: no channel/session to scope consent —
    refused`` across one day, all from ``job:incident_escalation``, with
    ``has_session`` TRUE and ``channel`` NULL. So every time the RCA concluded
    "build this tool" the build was refused — the self-healing loop diagnosing
    correctly and then unable to act. ``tool_build``'s own comment names it: "no
    LANE at all, so there is nothing to scope a grant or an audit record to. That
    is a WIRING FAULT, not an autonomy case." ``TraceContext.start`` has accepted
    ``channel`` all along; this never passed one.

    A job with a real target uses it — morning_brief's consent scope is telegram,
    not a placeholder. "internal" is NOT invented here: ``sessions_spawn``,
    ``sessions_send`` and ``delegate_task`` already write
    ``str(ctx.get("channel") or "internal")`` for a lane with no user channel, so
    this reuses that vocabulary rather than minting a second one.

    ``interactive`` STAYS FALSE. Giving the job a lane must not make it look like
    a human is attached; that would invert consent in the other direction, which
    is the failure the authority-versus-action note already records.
    """
    targets = getattr(job, "target_channels", None) or ()
    if isinstance(targets, str):  # an undecoded JSON column is not a list
        targets = ()
    channel = (
        getattr(job, "primary_channel", None)
        or (targets[0] if targets else None)
        or "internal"
    )
    return TraceContext.start(session_key=f"job:{job.job_id}", channel=str(channel))


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
        inflight: set[asyncio.Task[None]] = set()
        while True:
            # Fire this cycle as its OWN task instead of awaiting it inline — a
            # cycle containing a hung/slow handler (bounded by
            # _HANDLER_TIMEOUT_SEC, up to 20min) must never delay the NEXT
            # cycle's scan for newly-due jobs. _poll's own concurrent dispatch
            # (asyncio.gather) already made jobs WITHIN one cycle independent
            # of each other; this makes CYCLES independent of each other too —
            # a chronically-hung heavy job (the since-deleted dream_worker) was
            # still blocking this outer
            # loop from ever reaching its next tick while ITS gather was still
            # pending, starving telegram_canary for up to 20-40min at a
            # stretch even after the within-cycle fix. Safe: overlapping
            # pollers are EXACTLY what _run_job's CAS claim
            # (`UPDATE jobs SET status='running' WHERE status='pending'`) was
            # built for — a job already claimed by an earlier in-flight cycle
            # is simply excluded from the next cycle's `WHERE status='pending'`
            # select, so it can never be double-dispatched.
            task = asyncio.create_task(self._poll_cycle())
            inflight.add(task)
            task.add_done_callback(inflight.discard)
            await self._clock.async_sleep(_POLL_INTERVAL_SEC)

    async def _poll_cycle(self) -> None:
        """One timed, error-isolated poll tick — see run()'s fire-and-forget
        rationale. A raise here (e.g. a DB hiccup in the due-jobs fetch itself,
        outside any single job's gather) must not silently kill this cycle's
        task without a trace — logged, never propagated, matching _poll's own
        per-job isolation."""
        t0 = self._clock.monotonic()
        try:
            # A row abandoned past the handler timeout can never be claimed again
            # — the CAS claim only selects status='pending' — and the startup
            # reaper is the ONLY thing that used to free one, so the job stayed
            # dead until a restart. Reaped here, before the due-jobs fetch, so a
            # freed row is eligible in the SAME cycle. Isolated inside the same
            # try: a reap failure must never cost the poll.
            await reap_timed_out_running(self._db)
            await self._poll()
        except Exception as exc:
            log.heartbeat.error(
                "[scheduler] _poll_cycle: poll raised", exc_info=exc,
            )
        elapsed = (self._clock.monotonic() - t0) * 1000
        log.heartbeat.debug(
            "[scheduler] run: poll cycle complete",
            extra={"_fields": {"duration_ms": elapsed}},
        )

    async def _poll(self) -> None:
        TestModeGuard.assert_not_test_mode("scheduler.execute")
        now_iso = datetime.now(UTC).isoformat()
        # STEER-5/F113 — a job is due on its canonical recurring slot
        # (next_run_at) UNLESS a retry is pending, in which case retry_at alone
        # governs. retry_at is NULL for a healthy job, so the steady state is the
        # plain next_run_at select.
        #
        # WHY THIS IS NOT AN `OR`, which is how it was written until 2026-08-09.
        # `next_run_at` is advanced on COMPLETION (_mark_completed) or on
        # terminal re-arm (_mark_failed) — never when a retry is scheduled. So a
        # job that fails still has next_run_at IN THE PAST, the first arm of the
        # OR matched on the very next poll, and the 5-minute retry delay was
        # dead: measured on the live platform, owl_lifecycle-Brain (a job that
        # runs every TWO HOURS) failed three times 30 seconds apart — one per
        # scheduler tick — and burned its entire retry budget in 90 seconds.
        #
        # That is the difference between a retry budget that rides out a
        # transient outage and one that is guaranteed to be spent while the
        # outage is still happening. The DNS failure on 2026-08-08 lasted 25
        # minutes; three retries at their intended 5-minute spacing cover 15 of
        # them, three at 30 seconds cover none.
        rows = await self._db.fetch_all(
            "SELECT * FROM jobs WHERE status = 'pending' AND enabled = 1 "
            "AND (CASE WHEN retry_at IS NOT NULL THEN retry_at <= ? "
            "          ELSE next_run_at <= ? END)",
            (now_iso, now_iso),
        )
        if not rows:
            return
        # Concurrent dispatch — a sequential `for row: await self._run_job(...)`
        # let one slow-but-legitimate handler (the since-deleted dream_worker's ~20-30min
        # cycles) block every OTHER due job's on-time firing for its full
        # duration: telegram_canary's own delivery log showed a clean 20m/20m/
        # ~50m rhythm locked to that job's 30m cadence, not a real send
        # failure. _run_job's CAS claim (`UPDATE jobs SET status='running'
        # WHERE status='pending'`) already exists specifically so concurrent
        # dispatchers can never double-run the same job — this loop just never
        # used it. return_exceptions=True + explicit logging below: one job's
        # unexpected raise must not crash the poll cycle for every other job.
        results = await asyncio.gather(
            *(self._run_job(row_to_job(row)) for row in rows),
            return_exceptions=True,
        )
        for row, result in zip(rows, results, strict=True):
            if isinstance(result, BaseException):
                log.heartbeat.error(
                    "[scheduler] _poll: concurrent job dispatch raised",
                    exc_info=result,
                    extra={"_fields": {"job_id": row["job_id"]}},
                )


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
        # `claimed_at` is stamped BY THE CLAIM, in the same statement, so it can
        # never disagree with the status it describes. It is deliberately never
        # CLEARED: every claim overwrites it, and every reader gates on
        # status='running', so a leftover value on a pending row is unreadable by
        # construction. Clearing would mean editing all twelve sites that move a
        # job out of 'running' — and missing one is this codebase's most common
        # defect, an actuator wired on only some paths.
        await self._db.execute(
            "UPDATE jobs SET status = 'running', claimed_at = ? "
            "WHERE job_id = ? AND status = 'pending'",
            (datetime.now(UTC).isoformat(), job.job_id),
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

        # PATHFINDER-2026-07-22 Proposal 5 — a scheduled job never constructs a
        # PipelineState/goes through backend.run() UNLESS its handler itself
        # does (goal_execution does; evolution/critic_scorer/tool_outcome_miner
        # call providers directly), so retry_ledger would otherwise never be
        # bound for those handlers' own provider calls — a circuit-breaker-open
        # during a scheduled job was previously invisible anywhere. Binding
        # HERE, at the one central dispatch point every handler funnels
        # through, covers all of them with one change (nested bind inside a
        # handler that also calls backend.run() just isolates as designed —
        # see retry_ledger.py's docstring on nested-bind semantics).
        retry_ledger_token = retry_ledger.bind()
        # SAME SITE, SAME REASON as the retry_ledger bind above: the handlers that
        # call providers directly never pass through backend.run(), so nothing ever
        # bound a TraceContext for them and every one of their cost rows recorded a
        # blank trace_id. See _bind_job_trace for the measurement and for why the
        # trace is per-RUN while the job identity rides session_key.
        trace_token = _bind_job_trace(job)
        try:
            result = await asyncio.wait_for(handler.execute(job), timeout=_HANDLER_TIMEOUT_SEC)
        except TimeoutError:
            duration_ms = (time.monotonic() - t0) * 1000
            # SAY WHAT ACTUALLY HAPPENED. This read "freed for retry/re-arm",
            # and neither half was true: asyncio.wait_for cancels the awaited
            # coroutine, NOT the tasks it spawned with create_task, so a handler
            # that spawns sub-agent turns keeps running — measured 2026-08-25,
            # incident_escalation logged "RCA complete" NINE MINUTES after its own
            # timeout — and the row stays 'running' until something reclaims it.
            # The old wording is what a reader uses to conclude the job recovered;
            # I concluded exactly that an hour after taking the measurement. Same
            # class as the stale last_error fixed the same day: state that
            # misnames what happened costs its reader the same detour every time.
            log.heartbeat.error(
                "[scheduler] %s: handler exceeded its timeout — the dispatch slot "
                "is released so other jobs run, but the handler was NOT stopped "
                "and its row stays 'running' until reaped",
                job.job_id,
                extra={"_fields": {
                    "job_id": job.job_id, "handler": job.handler_name,
                    "timeout_sec": _HANDLER_TIMEOUT_SEC, "duration_ms": duration_ms,
                    "handler_stopped": False,
                    "row_reclaimed_after_sec": _STALE_RUNNING_AFTER_SEC,
                }},
            )
            result = JobResult(
                job_id=job.job_id, success=False, output=None,
                error=f"handler timed out after {_HANDLER_TIMEOUT_SEC:.0f}s",
                duration_ms=duration_ms,
            )
        except Exception as exc:
            duration_ms = (time.monotonic() - t0) * 1000
            log.heartbeat.error(
                "[scheduler] %s: handler raised",
                job.job_id,
                exc_info=exc,
                extra={"_fields": {"job_id": job.job_id, "duration_ms": duration_ms}},
            )
            result = JobResult(job_id=job.job_id, success=False, output=None, error=str(exc), duration_ms=duration_ms)
        finally:
            _retry_events = retry_ledger.get_retry()
            if _retry_events:
                log.heartbeat.info(
                    "[retry] job summary",
                    extra={"_fields": {
                        "job_id": job.job_id,
                        "handler": job.handler_name,
                        "events": [
                            {"kind": e.kind, "provider": e.provider, "detail": e.detail,
                             "attempt_number": e.attempt_number}
                            for e in _retry_events
                        ],
                    }},
                )
            retry_ledger.reset(retry_ledger_token)
            # Reset in the SAME finally: a ContextVar left set would attribute the
            # next job's spend to this one.
            TraceContext.reset(trace_token)

        duration_ms = (time.monotonic() - t0) * 1000
        # PB6a — a job that self-reports success=True but was checked and found
        # verified=False (claimed-but-not-observed) is never a trustworthy win;
        # route it through the same retry/terminal-fail path as an ordinary
        # failure. verified=None (every un-migrated handler) falls back to
        # `success` unchanged — byte-identical to pre-existing dispatch.
        if is_trustworthy_success(result.success, result.verified):
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
        run_id = str(uuid.uuid4())
        # A ONE-SHOT HAS NO NEXT OCCURRENCE, and this is the path that kept giving
        # it one. MEASURED 2026-08-31: 92 `rollover_summary` rows on the live table,
        # every one status='pending' with last_run_at today and next_run_at TOMORROW,
        # armed to re-summarise conversation boundaries that happened once, days ago.
        # The log carries the mechanism 326 times since 2026-08-28: "compute_next_run:
        # cron parse failed — defaulting to +1d  {'schedule': 'manual'}".
        #
        # THE MARKER ALREADY EXISTED AND THIS PATH DID NOT ASK IT.
        # `enqueue_rollover_summary` sets `run_once` and its comment names this exact
        # failure; `_is_recurring` reads it; `_idempotent_skip` asks it. Only here did
        # the rule have a second, silent copy — recompute the cadence unconditionally.
        #
        # NOT the schedule string: "manual" is not a cadence, so tightening
        # compute_next_run would only change WHICH wrong cadence a one-shot gets.
        one_shot = not self._is_recurring(job)
        next_run = job.next_run_at if one_shot else compute_next_run(
            job.schedule, tz=self._tz,
        )
        # 'completed' is in the jobs CHECK constraint and was written by NOTHING —
        # 126 pending, 1 failed, 0 completed. Terminal, not deleted: `job_runs.job_id`
        # is a real FK and the run history is the record of what the platform did, so
        # the row stays inspectable while dropping out of the claim query, which
        # selects `status='pending' AND enabled=1`.
        status = ONE_SHOT_TERMINAL_STATUS if one_shot else "pending"
        # STEER-5/F113 — on success, recompute the canonical cadence AND clear the
        # transient retry state (retry_count=0, retry_at=NULL) so a previously
        # flaky job returns to a clean steady state on its real schedule. Also reset
        # failure_count: it counts CONSECUTIVE failed runs — one success starts a
        # fresh streak (surfaced via the F-61 per-re-arm alert, no breaker anymore).
        #
        # ROOT-CAUSE FIX (QA/Murat, live reminder crash) — a run_once handler
        # (goal_execution) self-deletes its OWN jobs row on success BEFORE
        # returning here. ``rowcount`` from THIS update tells us, for free,
        # whether that row still exists: 0 rows means the handler already
        # removed it. job_runs.job_id is a real FK to jobs (foreign_keys=ON in
        # production) — inserting a job_runs row for a job_id that no longer
        # exists raises sqlite3.IntegrityError, uncaught, which used to burn one
        # of the supervisor's 5 consecutive-failure lives PER successful
        # reminder and could park the entire scheduler (every recurring job
        # too) permanently failed. job_runs is a dedup/history table only
        # (migration 0040: "no other table references it") and a deleted
        # one-shot can never be re-polled, so skipping its row here loses
        # nothing.
        rows_affected = await self._db.execute_returning_rowcount(
            # `last_error` is cleared with the rest of the failure record. It is
            # ONE record — retry_count, retry_at, failure_count and the reason —
            # and clearing three of four left rows claiming zero failures beside a
            # populated error string. Measured 2026-08-24: three healthy jobs
            # (skill_synthesizer, retry_sweep, and the since-deleted dream_worker)
            # were reporting errors
            # from runs that had long since succeeded, and the string is read back
            # through scheduler_helpers into the TUI job list. requeue(),
            # owl_lifecycle and resume_job already clear it on recovery; this was
            # the one path that did not.
            "UPDATE jobs SET status = ?, last_run_at = ?, next_run_at = ?, "
            "retry_count = 0, retry_at = NULL, failure_count = 0, last_error = NULL "
            "WHERE job_id = ?",
            (status, now_iso, next_run, job.job_id),
        )
        if rows_affected == 0:
            log.heartbeat.info(
                "[scheduler] %s: exit — completed (job self-deleted by handler, "
                "job_runs insert skipped)",
                job.job_id,
                extra={"_fields": {"job_id": job.job_id, "duration_ms": duration_ms}},
            )
            return
        await self._db.execute(
            "INSERT INTO job_runs (run_id, job_id, idempotency_key, status, duration_ms, ran_at) VALUES (?,?,?,?,?,?)",
            (run_id, job.job_id, self._occurrence_key(job), "completed", duration_ms, now_iso),
        )
        log.heartbeat.info(
            "[scheduler] %s: exit — completed",
            job.job_id,
            extra={"_fields": {
                "job_id": job.job_id, "duration_ms": duration_ms,
                "next_run": None if one_shot else next_run,
                "one_shot": one_shot,
            }},
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

    async def _rearm_one_shot(
        self, job: Job, last_error: str | None, failure_class: str
    ) -> None:
        """Re-queue a one-shot that failed on something that may yet pass (ESC-53).

        Mirrors the recurring re-arm: transient retry state is cleared and the row
        returns to ``pending``. The difference is WHEN — a one-shot has no cadence
        slot to advance to, so the next attempt is placed on a backoff ladder that
        widens with ``failure_count`` and then holds at its last entry. That bounds
        the RATE without ever bounding the number of attempts, which is what
        "never give up on the job" has to mean for work that cannot regenerate
        itself by waiting.

        ``last_error`` is deliberately KEPT: the row is waiting, and a row that is
        waiting must still say what it is waiting on.
        """
        attempt = (job.failure_count or 0) + 1
        idx = min(attempt - 1, len(_ONE_SHOT_REARM_BACKOFF_SEC) - 1)
        delay = _ONE_SHOT_REARM_BACKOFF_SEC[idx]
        next_run = (datetime.now(UTC) + timedelta(seconds=delay)).isoformat()
        now_iso = datetime.now(UTC).isoformat()
        await self._db.execute(
            "UPDATE jobs SET status = 'pending', last_run_at = ?, next_run_at = ?, "
            "retry_count = 0, retry_at = NULL, failure_count = ?, "
            "last_error = ? WHERE job_id = ?",
            (now_iso, next_run, attempt, last_error, job.job_id),
        )
        # INFO, not DEBUG: this line is the evidence that a one-shot survived a
        # transient fault, and production runs at INFO.
        log.heartbeat.info(
            "[scheduler] %s: one-shot RE-ARMED after transient failure — retry in %ss",
            job.job_id,
            delay,
            extra={"_fields": {"job_id": job.job_id, "handler": job.handler_name,
                               "attempt": attempt, "delay_sec": delay,
                               "failure_class": failure_class or "unknown",
                               "next_run": next_run}},
        )
        await write_audit(
            self._db,
            "job_rearmed_one_shot",
            job.job_id,
            actor="scheduler",
            details={"handler": job.handler_name, "attempt": attempt,
                     "delay_sec": delay, "failure_class": failure_class,
                     "last_error": last_error},
        )
        await self._notify_failure(
            job, last_error, terminal=False,
            disposition=(
                f"failed transiently and will retry in {delay}s (attempt {attempt})"
            ),
        )

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
            # ESC-53 — a one-shot dies terminally only when repeating it CANNOT
            # work. Anything else re-arms with backoff, because the alternative is
            # what actually happened: 54 conversation rollover summaries lost
            # across 16 days because a provider circuit was open for minutes. The
            # recurring branch below has re-armed since F-60 with the explicit
            # note that "the job itself is never given up on"; whether work
            # survived a blip was being decided by CADENCE, which has nothing to
            # do with whether the work still needs doing.
            failure_class = classify_failure(last_error)
            if not is_permanent(failure_class):
                await self._rearm_one_shot(job, last_error, failure_class)
                return
            await self._db.execute(
                "UPDATE jobs SET status = 'failed', last_error = ? WHERE job_id = ?",
                (last_error, job.job_id),
            )
            log.heartbeat.error(
                "[scheduler] %s: one-shot marked permanently failed — %s",
                job.job_id,
                failure_class,
                extra={"_fields": {"job_id": job.job_id, "retries": job.retry_count + 1,
                                   "failure_class": failure_class}},
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

        # No consecutive-failure circuit breaker (owner decision 2026-07-22) — a
        # recurring job that keeps failing keeps re-arming onto its next cadence
        # slot instead of being permanently paused; the operator still gets an
        # alert on every failure via the re-arm path below (F-61), so ongoing
        # trouble is never silent, but the job itself is never given up on.
        new_failure_count = job.failure_count + 1

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
        # Every recurring job (owl-lifecycle included) gets the F-61 per-re-arm
        # operator alert (owner decision 2026-07-22): with the circuit breaker
        # removed there is no longer a threshold notification to fall back on,
        # so a job that keeps failing must not go silent.
        await self._notify_failure(job, last_error, terminal=False)

    async def _notify_failure(
        self,
        job: Job,
        last_error: str | None,
        *,
        terminal: bool,
        disposition: str | None = None,
    ) -> None:
        """Route an operator alert for a retry-exhausted job (F-61).

        A job that exhausts its retries — whether it dies (one-shot ``terminal``)
        or re-arms onto its next slot (recurring — every recurring failure gets
        an alert now that there is no consecutive-failure circuit breaker to fall
        back on) — is an outage whose only prior signal was a buried ERROR log
        line. This pushes a proactive notification through the SAME delivery seam
        (:class:`ProactiveJobDeliverer`) that morning_brief / check_in /
        goal_execution use, addressed from the job's OWN durable recipients
        (``target_channels`` / ``target_addresses``).

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
        # `disposition` is overridable because the default non-terminal wording
        # ("re-armed to its next slot") describes a RECURRING job's cadence, and a
        # re-armed one-shot has no slot. An alert that misnames its own cause
        # costs its reader the same detour every time — the defect fixed in
        # web_fetch and again in the stale last_error, and not one to reintroduce
        # in the very alert that reports a rescue.
        # THE SUFFIX BELONGS TO THE DISPOSITION, NOT TO THE TEMPLATE. "after
        # exhausting retries" used to be appended to EVERY alert, including the
        # transient-retry one at the caller above — producing "failed transiently
        # and will retry in 60s (attempt 3) after exhausting retries", which says
        # both that it will retry and that it has run out of retries. Exactly the
        # self-misnaming this method's own comment warns about, in its own
        # sentence. An exhaustion alert says so; a transient one does not.
        exhausted = disposition is None
        if exhausted:
            disposition = (
                "permanently failed after exhausting retries" if terminal
                else "is failing repeatedly and has been re-armed to its next slot"
            )
        # NAME WHICH JOB, AND FOR THE HOW-MANIETH TIME. ``handler_name`` is the
        # job's TYPE, not its identity, so nine different job rows running the
        # same handler and hitting the same error composed byte-identical text.
        # MEASURED 2026-09-03: of 262 job_failed notifications carrying 31
        # distinct messages, ONE text was delivered 176 TIMES ACROSS 9 DISTINCT
        # job_ids — he was told "this job is failing repeatedly" 176 times and
        # could not tell which of the nine it meant. It is also why the
        # per-(job_id, channel) frequency cap saw each of those as a first send.
        #
        # The occurrence number is the same expression the audit row already
        # uses a hundred lines up — asked, not restated, so the alert and the
        # ledger can never disagree about which attempt this was. It makes
        # repetition a COUNTER rather than the same sentence again, which is the
        # one improvement here that suppresses nothing: this tree has already
        # decided to FAIL TOWARD PAGING (lesson_recurrence.already_paged), and
        # whether identical alerts should ever be held back is ESC-117, his call.
        message = f"Scheduled job '{job.handler_name}' ({job.job_id}) {disposition}."
        if exhausted:
            # ``failure_count`` counts EXHAUSTIONS, and only the exhaustion paths
            # increment it — so the counter is meaningful here and would be a
            # confident wrong number on the transient path, which carries its own
            # "(attempt N)" already.
            message += f" Failure #{(job.failure_count or 0) + 1} for this job."
        if last_error:
            message += f" Last error: {last_error}"
        try:
            outcome = await self._job_deliverer.deliver_for_job(
                job,
                message=message,
                category="job_failed",
                # "high" is not a valid Notification.urgency literal (critical/
                # normal/low only) — it raised inside Notification.__init__ AFTER
                # the ledger had already claimed the occurrence's dispatch slot,
                # permanently stranding every failure alert at "dispatched" and
                # silently swallowing the exception (B5 catch below). "critical"
                # matches the router's "always delivered, bypasses quiet hours"
                # semantics an outage alert needs (same tier goal_execution uses
                # for a user-queued run_once delivery).
                urgency="critical",
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
            "last_error = NULL, circuit_broken_at = NULL, next_run_at = ? WHERE job_id = ?",
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
        await reap_stale_running(self._db, tz=self._tz)
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
            # A ONE-SHOT job (run_once — e.g. a cronjob "in 5m" reminder) has no
            # sensible "next slot": the else branch's compute_next_run(job.schedule)
            # would recompute a RELATIVE schedule ("in 5m") fresh from THIS boot's
            # `now`, silently pushing the user's specific requested time further
            # into the future on every restart before it ever fires — a reminder
            # can be deferred forever by repeated restarts and never actually be
            # delivered. Always replay an overdue one-shot (bounded by the same
            # window), regardless of replay_missed — deferring it is data loss,
            # not a benign reschedule, unlike a recurring job which gets another
            # occurrence soon regardless.
            if (job.replay_missed or not self._is_recurring(job)) and inside_window:
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
        return await update_job(
            self._db, job_id, schedule=schedule, goal=goal, params=params, tz=self._tz
        )

    async def run_now(self, job_id: str) -> JobResult | None:
        """Run one job out of band — thin delegate; mirrors the poller's CAS (B2)."""
        return await run_now(self._db, self._clock, self._registry, job_id, tz=self._tz)
