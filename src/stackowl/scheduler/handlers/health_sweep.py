"""HealthSweepHandler (F-87) — periodic in-process health DETECTION + alert.

Health was previously detect-only and ON-DEMAND: nothing ran the
:class:`HealthAggregator` except the out-of-process ``stackowl health`` CLI, so a
subsystem that silently went ``down`` while the service ran was never noticed and
never triggered any response. This handler closes the detect half of that gap: a
recurring scheduler job collects health from the live in-process aggregator and,
on any ``down``/``degraded`` subsystem, emits a LOUD operator log and (when wired)
pushes a proactive operator alert.

Deferred (flagged, not done here): AUTO-RECYCLE of an unhealthy resource. Driving
``attempt_with_recycle`` requires the live :class:`ResilienceContributor` with
``HealableResource`` refs (browser runtime, db pool, providers) threaded from the
serve process — a larger wiring change. This handler is the safe periodic
detect+alert subset; recycle remains a follow-up.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

import aiosqlite

from stackowl.db.pool import DbPool
from stackowl.health.status import (
    HEALTHY_STATES,
    LIVENESS_FAILING_STATES,
    WARNING_STATES,
)
from stackowl.infra.clock import Clock, WallClock
from stackowl.infra.observability import log
from stackowl.journal import ActorKind, JournalEvent, Outcome, RecordRef
from stackowl.journal import record as journal_record
from stackowl.journal.enums import classify_health_error
from stackowl.journal.heal_events import (
    HealAttemptedAttrs,
    HealExhaustedAttrs,
    HealHealedAttrs,
)
from stackowl.journal.health_events import HealthChangedAttrs
from stackowl.scheduler.base import JobHandler
from stackowl.scheduler.job import Job, JobResult

#: A subsystem's health-status name is already bounded (`HealthStatus.name`)
#: well inside AD-4's 64-char attrs bound -- no truncation needed, but the
#: constant is named here so a future field knows the ceiling it must respect.
_ACTOR_ID = "health_sweep"


def clear_degraded_if_a_provider_is_back(
    statuses: Sequence[HealthStatus], services: object | None
) -> bool:
    """Clear the boot-time providers-degraded latch when the sweep proves a provider ok.

    WHY THIS LIVES IN THE SWEEP. `_maybe_reprobe_providers` already re-heals the latch,
    but only from `_dispatch_turn` — the inbound-MESSAGE path. Every other turn (a
    scheduled job, the durable task loop, the RCA lane) reads the value stamped onto
    `StepServices` at boot and floors. So the platform could self-heal a provider outage
    ONLY IF A HUMAN TALKED TO IT, which is backwards for unattended work: recovery
    matters most exactly when nobody is there to trigger it. Operator-reported 2026-09-05
    after a VPN outage left the platform degraded for hours while `ProviderContributor`
    probed successfully every five minutes and discarded the answer. Six turns floored.

    NOT A SECOND PROBER. The sweep already probes every provider and already runs; this
    only stops it throwing the verdict away — one component asking the other rather than
    a new engine.

    FAILS CLOSED. Requires an explicit `ok` from a `provider:` contributor. A sweep with
    no provider status has learned nothing about providers, and `degraded` is not `ok` —
    the probe distinguishes those deliberately. Never raises: this runs inside the sweep
    that every other subsystem's alerting depends on.

    Returns True only when it actually cleared the latch.
    """
    try:
        if services is None or not getattr(services, "providers_degraded", False):
            return False
        healthy = [
            s for s in statuses
            if s.name.startswith("provider:") and s.status in HEALTHY_STATES
        ]
        if not healthy:
            return False
        services.providers_degraded = False  # type: ignore[attr-defined]
        log.scheduler.info(
            "[scheduler] health_sweep: providers no longer degraded — a provider "
            "answered the sweep, so turns resume without waiting for a message",
            extra={"_fields": {"recovered": [s.name for s in healthy]}},
        )
        return True
    except Exception as exc:  # noqa: BLE001 — must never wedge the sweep
        log.scheduler.warning(
            "[scheduler] health_sweep: degraded-latch check failed", exc_info=exc,
        )
        return False


class AlertRecord(Protocol):
    """The durable record of what the operator has already been paged about.

    Two halves, and both are needed: a backoff that READS a record nothing
    writes suppresses nothing, and a record nothing reads is a write with no
    reader. Kept as a Protocol so the handler owns the RULE and the storage stays
    where storage belongs.
    """

    async def load_recent_alerts(self, within_s: float) -> dict[str, tuple[str, float]]:
        """``name -> (status, seconds since it was last alerted)``, within the window."""
        ...

    def record_alert(self, name: str, status: str) -> None:
        """Note that the operator was just paged about ``name`` at ``status``."""
        ...


if TYPE_CHECKING:
    from stackowl.health.aggregator import HealthAggregator
    from stackowl.health.status import HealthStatus
    from stackowl.infra.resilience import HealableResource
    from stackowl.pipeline.recovery_actuator import RecoveryActuator

# An operator-alert sink: receives an already-composed alert message. Async.
AlertSink = Callable[[str], Awaitable[None]]

#: How often the sweep runs. ONE number: the scheduler seeds the job from it, and a
#: contributor that must not act on anything younger than one sweep asks it.
HEALTH_SWEEP_INTERVAL_MINUTES = 5


def _health_loop_enabled() -> bool:
    """ADR-6 flag read — module-level so tests can monkeypatch it. Never raises."""
    try:
        from stackowl.config.settings import Settings

        return bool(Settings().health_loop)
    except Exception:  # noqa: BLE001 — a flag read must never wedge the sweep
        return False


def _sweep_gap(job: Job) -> tuple[float | None, int]:
    """(seconds since this sweep last ran, whole runs missed).

    YOU CANNOT DETECT YOUR OWN ABSENCE WHILE ABSENT — BUT YOU CAN ON RETURN.

    MEASURED 2026-09-09 on the incident of 2026-09-07: health_sweep entries ran
    10-11 an hour all day, then 5 / 0 / 0 / 0 / 4 across hours 14-18, while the
    database failed 419 / 838 / 839 / 838 / 520 times in those same hours. THREE
    COMPLETE HOURS with no sweep at all, and the monitor was silent exactly when
    there was most to report.

    The sweep is a scheduled job, the scheduler reads `jobs` to dispatch it, and the
    database was down — so the monitor shares a failure domain with the thing it
    monitors. Worse, the check that would have noticed (`store_cadence`, which already
    watches `jobs.last_run_at` for staleness) is a CONTRIBUTOR TO THE SWEEP: the
    detector for "the sweep stopped" was inside the sweep.

    Nothing in this process can report while it cannot reach its database, and the
    external-watchdog half is already settled (ESC-159: "leave this box alone — the
    question is the CUSTOMER deployment"). What was missing is smaller and entirely
    in reach: when the sweep RESUMED at 18:37 it logged an ordinary exit, identical
    to the ten before the incident. Nothing said "there was no monitoring for four
    hours", so the gap was recoverable only by counting log lines per hour by hand.

    `job.last_run_at` holds the PREVIOUS run's stamp at execute time — the completion
    UPDATE that overwrites it runs after the handler — so this needs no new plumbing
    and no second source. The interval comes from `parse_every`, the same function
    `compute_next_run` and `is_valid_schedule` use, so the sweep cannot disagree with
    the scheduler about its own cadence.

    MISSED RUNS, NOT SECONDS. A seconds threshold would be a constant nobody could
    justify and would rot the moment the cadence changed. `gap / interval - 1` is
    derived from the job's own schedule, so a five-minute sweep and a daily one are
    both right without a second number.

    Degrades to silence, never to a false alarm: a first-ever run, a cron-style
    schedule with no fixed interval, or an unparseable stamp all report nothing.
    """
    from datetime import UTC, datetime

    from stackowl.scheduler.scheduler_helpers import parse_every

    if not job.last_run_at:
        return None, 0
    try:
        previous = datetime.fromisoformat(job.last_run_at)
        if previous.tzinfo is None:
            previous = previous.replace(tzinfo=UTC)
        gap = (datetime.now(UTC) - previous).total_seconds()
    except (ValueError, TypeError):
        # A stamp this cannot read is a reason to say nothing, not to guess. Never
        # raises: losing the gap costs a log field, losing the sweep costs the sweep.
        return None, 0
    interval = parse_every(job.schedule or "")
    if interval is None or interval.total_seconds() <= 0:
        return gap, 0
    return gap, max(0, int(gap // interval.total_seconds()) - 1)


def _log_exit(
    job: Job, *, verdict: str, total: int, duration_ms: float, **extra: int
) -> None:
    """ONE countable INFO line per sweep, whatever the outcome.

    WHY IT IS ONE LINE AND NOT THREE. The verdict used to BE the message —
    `all healthy` at DEBUG, `UNHEALTHY subsystems detected` at ERROR,
    `subsystems RECOVERED after heal` at WARNING — so counting sweeps meant
    counting three different strings at three levels, one of which production
    never records. MEASURED 2026-09-08 over the retained window: 2,705 sweeps
    dispatched, 819 UNHEALTHY, 50 RECOVERED, and ZERO `all healthy`. Every
    failure visible, no success visible, so "819 unhealthy" could equally be
    819-of-819 or 819-of-2,705 and the logs could not say which.

    A verdict carried as a FIELD can be grouped into that ratio; a verdict
    carried as a message identity cannot. The loud per-outcome lines above stay
    exactly as they are — an unhealthy subsystem is still an ERROR — this only
    adds the line that makes them countable.
    """
    gap_s, missed = _sweep_gap(job)
    if missed >= 2:
        # ANNOUNCED, NOT MERELY RECORDED. The field below makes the gap countable
        # afterwards; it is not a signal. Two missed runs is the floor because one is
        # jitter — a slow handler or a busy box — and warning on jitter would train
        # the reader to skip the line that matters.
        log.scheduler.warning(
            "[scheduler] health_sweep: MONITORING WAS BLIND — this sweep is the "
            "first since a gap, and nothing was watching the platform during it",
            extra={"_fields": {
                "job_id": job.job_id, "missed_runs": missed,
                "since_previous_s": round(gap_s or 0.0, 1), "schedule": job.schedule,
            }},
        )
    log.scheduler.info(
        "[scheduler] health_sweep.execute: exit",
        extra={"_fields": {
            "job_id": job.job_id, "verdict": verdict, "total": total,
            "duration_ms": duration_ms,
            # COMPUTED HERE, in the ONE exit helper, because there are four
            # `_log_exit` call sites and doing it at each would be the
            # actuator-wired-on-some-paths shape this repo names first.
            "since_previous_s": round(gap_s, 1) if gap_s is not None else None,
            "missed_runs": missed,
            **extra,
        }},
    )


class HealthSweepHandler(JobHandler):
    """Runs :meth:`HealthAggregator.collect` and alerts on unhealthy subsystems.

    ADR-6: when ``settings.health_loop`` is ON and a down/degraded subsystem has a
    registered :class:`HealableResource` in ``healers``, the sweep closes the loop —
    recycle (``ensure_available``, retry-bounded via the ADR-2 RecoveryActuator) then
    RE-COLLECT to verify; only a subsystem still down after the heal escalates. With no
    healers (today's wiring) or the flag OFF the sweep is the pre-ADR detect+alert path.
    """

    def __init__(
        self,
        aggregator: HealthAggregator,
        *,
        db: DbPool,
        alert: AlertSink | None = None,
        # Mapping, not dict: this only ever does `.get()` and a truthiness check,
        # and ChannelHealers is a Mapping that resolves channel adapters at LOOKUP
        # time — the ordering fix that closed the never-wired channel self-heal.
        healers: Mapping[str, HealableResource] | None = None,
        recovery: RecoveryActuator | None = None,
        clock: Clock | None = None,
        realert_backoff_s: float = 3600.0,
        alert_record: AlertRecord | None = None,
    ) -> None:
        self._aggregator = aggregator
        # Story 2.6 — the transaction home for heal.*/health.changed journal
        # events, kept alongside their heal_attempts/health_status_changes rows
        # (AD-24). Required, matching every other handler that took on a real
        # DB dependency (assembly already has `db` in scope at construction).
        self._db = db
        self._alert = alert
        self._healers = healers or {}
        self._recovery = recovery
        self._clock = clock or WallClock()
        self._realert_backoff_s = realert_backoff_s
        # Live-alert dedup state (F-88-ish): subsystem name -> (last-alerted
        # status, monotonic() at that alert).
        #
        # IT DOES NEED TO SURVIVE A RESTART, and the comment that used to stand
        # here said otherwise: "a fresh process re-alerts once on the next
        # unhealthy tick, which is fine". That was an assumption about how often
        # this process restarts, and it was never measured. MEASURED 2026-09-03
        # over one continuous 13-hour provider outage: 37 critical Telegram pages
        # where the one-hour heartbeat intends 13, with 11 of them landing within
        # three minutes of a boot. CodeWatcher exec-replaces the core on every
        # code change, so "once per restart" was the DOMINANT source, not a rare
        # extra.
        #
        # Per-process state doing a durable job — the sibling handler's own words
        # about its own first version (capability_gap_escalation, one scope
        # narrower), and it takes the same cure: an existing store, no new engine.
        # `alert_record` is that store when wired; unwired, this is byte-identical
        # to the previous behaviour.
        self._alert_state: dict[str, tuple[str, float]] = {}
        self._alert_record = alert_record
        self._state_loaded = False
        # THE LIVE `StepServices` THE DISPATCH LOOP READS — bound after construction,
        # because it does not exist yet. Scheduler assembly runs at orchestrator:1251
        # and that object is built at :1625, so there is nothing to pass in here. Same
        # ordering problem, and the same cure, as `healers=ChannelHealers(...)` above:
        # resolve when the sweep LOOKS, not when this assembly runs.
        self._live_services: object | None = None
        self._warned_unbound = False

    def bind_live_services(self, services: object) -> None:
        """Hand the sweep the one ``StepServices`` the gateway's dispatch loop reads.

        Called by the orchestrator once that object exists. Without it the degraded
        latch cannot be cleared and the platform is back to self-healing only when a
        human sends a message — so an unbound sweep SAYS so rather than doing nothing
        quietly.
        """
        self._live_services = services
        log.scheduler.info(
            "[scheduler] health_sweep: bound to the live pipeline services — a "
            "recovered provider now clears the degraded latch unattended",
        )

    @property
    def handler_name(self) -> str:
        return "health_sweep"

    async def execute(self, job: Job) -> JobResult:
        # 1. ENTRY
        log.scheduler.debug(
            "[scheduler] health_sweep.execute: entry",
            extra={"_fields": {"job_id": job.job_id}},
        )
        t0 = time.monotonic()
        try:
            # 3. STEP — collect current health from every registered contributor.
            statuses = await self._aggregator.collect()
        except Exception as exc:  # never let a probe error wedge the scheduler
            duration_ms = (time.monotonic() - t0) * 1000
            log.scheduler.error(
                "[scheduler] health_sweep.execute: aggregator raised",
                exc_info=exc,
                extra={"_fields": {"job_id": job.job_id, "duration_ms": duration_ms}},
            )
            # 4. EXIT — counted like every other outcome. This is the path an
            # outage actually takes, so leaving it out would lose the sweeps that
            # matter most from the denominator.
            _log_exit(job, verdict="probe_failed", total=0, duration_ms=duration_ms)
            return JobResult(
                job_id=job.job_id,
                effect_class="delivery",
                success=False,
                output=None,
                error=str(exc),
                duration_ms=duration_ms,
            )

        # A RECOVERED PROVIDER MUST NOT WAIT FOR A HUMAN. The boot-time latch cleared
        # only on the inbound-message path, so scheduled and durable turns floored until
        # someone typed something. The sweep already holds the verdict.
        #
        # IT READS THE BOUND OBJECT, NEVER `get_services()`, and the first version of
        # this fix got that wrong in a way worth recording. `pipeline.services._ctx` is
        # a ContextVar and `get_services()` returns a FRESH EMPTY `StepServices` on
        # LookupError — so from the scheduler's own task context it handed back a new
        # object whose latch is False by default, the check returned immediately, and
        # the whole repair was decoration that could never log, never fail and never
        # fire. That is the exact defect shape this change exists to fix, reproduced
        # inside the fix; unit tests passed throughout because they call the function
        # with a services double. The latch that matters is the single instance built
        # at orchestrator:1625 and shared by every turn.
        if self._live_services is None and not self._warned_unbound:
            self._warned_unbound = True
            log.scheduler.warning(
                "[scheduler] health_sweep: NOT bound to the live pipeline services — "
                "a recovered provider will keep waiting for a human message; "
                "orchestrator must call bind_live_services()",
            )
        clear_degraded_if_a_provider_is_back(statuses, self._live_services)

        # FR84/Story 2.6 — compare THIS collect against each subsystem's last
        # recorded status and journal any real transition. Runs on EVERY tick
        # (not just an unhealthy one) so a subsystem recovering on its own
        # (never healed by this sweep) is captured too, and again after the
        # heal-triggered re-collect below.
        async with self._db.transaction() as conn:
            await self._record_health_changes(conn, statuses)

        down = [s for s in statuses if s.status in LIVENESS_FAILING_STATES]
        degraded = [s for s in statuses if s.status in WARNING_STATES]
        duration_ms = (time.monotonic() - t0) * 1000

        # 2. DECISION — all healthy → quiet exit; unhealthy → LOUD log + alert.
        if not down and not degraded:
            _, resolved = self._dedupe_and_update([], [])
            await self._maybe_send_resolved(resolved)
            # 4. EXIT. This used to be the ONLY record of a healthy sweep and it
            # was DEBUG, so across 2,705 real sweeps it appeared ZERO times while
            # 819 UNHEALTHY lines appeared at ERROR — every failure visible, no
            # success visible, and therefore no ratio. Replaced by the exit line
            # rather than raised to INFO beside it: one line per sweep carrying the
            # verdict as a FIELD is what makes the outcomes groupable.
            _log_exit(
                job, verdict="healthy", total=len(statuses), duration_ms=duration_ms,
            )
            return JobResult(
                job_id=job.job_id,
                effect_class="delivery",
                success=True,
                output=f"healthy={len(statuses)}",
                error=None,
                duration_ms=duration_ms,
                metadata={"down": 0, "degraded": 0, "total": len(statuses)},
            )

        # ADR-6 — HEAL → VERIFY (closed loop). Flag-gated; with no healers this block is
        # a no-op even ON, so it is byte-identical to the pre-ADR path. Recycle each
        # unhealthy subsystem that has a registered HealableResource, then RE-COLLECT to
        # observe whether reality recovered (ADR-1 style: verify, don't assume).
        attempted = await self._heal_and_verify(job, down, degraded)
        if attempted:
            statuses = await self._aggregator.collect()
            async with self._db.transaction() as conn:
                await self._record_health_changes(conn, statuses)
            down = [s for s in statuses if s.status in LIVENESS_FAILING_STATES]
            degraded = [s for s in statuses if s.status in WARNING_STATES]
            duration_ms = (time.monotonic() - t0) * 1000
            still_unhealthy = {s.name for s in (*down, *degraded)}
            healed = set(attempted) - still_unhealthy  # recycled AND re-verified ok
            # Spec 2.6 — the currently-missing exhaustion branch: a subsystem
            # a heal was ATTEMPTED for and is STILL unhealthy after this
            # re-verify has exhausted the sweep's own heal loop (NEEDS_YOU/HIGH,
            # AD-5's named example) — distinct from a subsystem no healer
            # covers at all, which never enters `attempted` and stays on the
            # ordinary down/degraded alert path below.
            exhausted = set(attempted) & still_unhealthy
            await self._resolve_heal_attempts(attempted, healed, exhausted)
            if healed:
                log.scheduler.warning(
                    "[scheduler] health_sweep.execute: subsystems RECOVERED after heal",
                    extra={"_fields": {"job_id": job.job_id, "healed": sorted(healed)}},
                )
            if exhausted:
                log.scheduler.error(
                    "[scheduler] health_sweep.execute: heal EXHAUSTED — still "
                    "unhealthy after its own re-verify",
                    extra={"_fields": {"job_id": job.job_id, "exhausted": sorted(exhausted)}},
                )
            if not down and not degraded:
                # 4. EXIT — every unhealthy subsystem was healed + re-verified. No alert.
                _, resolved = self._dedupe_and_update([], [])
                await self._maybe_send_resolved(resolved)
                _log_exit(
                    job, verdict="recovered", total=len(statuses),
                    duration_ms=duration_ms, healed=len(healed),
                )
                return JobResult(
                    job_id=job.job_id,
                    effect_class="delivery",
                    success=True,
                    output=f"healed={len(healed)}",
                    error=None,
                    duration_ms=duration_ms,
                    metadata={"down": 0, "degraded": 0, "healed": len(healed),
                              "total": len(statuses)},
                )

        await self._seed_alert_state()
        to_alert, resolved = self._dedupe_and_update(down, degraded)
        await self._maybe_send_resolved(resolved)

        message = _compose_alert(down, degraded)
        # This log fires every tick regardless of alert-sink dedup — dedup only
        # ever suppresses the OUTBOUND alert send below, never the operator log.
        log.scheduler.error(
            "[scheduler] health_sweep.execute: UNHEALTHY subsystems detected",
            extra={
                "_fields": {
                    "job_id": job.job_id,
                    "down": [s.name for s in down],
                    "degraded": [s.name for s in degraded],
                    # WHICH OF THEM TOLD THE OPERATOR WHAT TO DO (D14.4). The remedy
                    # itself travels in the outbound alert and in JobResult.output —
                    # neither of which is a log — so before this field there was NO
                    # observable record that a remedy had been attached at all. D14.4's
                    # first closing check grepped the logs for the alert text and could
                    # never have succeeded: `_compose_alert` RETURNS that string, and
                    # nothing logs it. Naming the subsystems here is what turns "did the
                    # 2am alert carry a fix?" into a question the logs can answer.
                    "remedies": [s.name for s in (*down, *degraded) if s.remedy],
                }
            },
        )
        # AND SAY IT AS A LITERAL WHEN THE ANSWER IS YES (DEBT-288).
        #
        # The field above is the right record and it cannot be a closing check's
        # pattern: `"remedies": ["` is JSON rendered at LOG time, so no AST walk over
        # `log.*` literals can see it, and the exemption list for that shape demands
        # live proof this string has never had — 0 of 822 unhealthy sweeps carried a
        # remedy before today. A sentence the code actually holds is checkable.
        #
        # Guarded on the LIST BEING NON-EMPTY, which is the branch a reader must
        # watch: the sweep firing is not the event, a remedy surviving to the sweep
        # is.
        remedied = [s.name for s in (*down, *degraded) if s.remedy]
        if remedied:
            log.scheduler.info(
                "[scheduler] health_sweep.execute: the diagnosis says what to do "
                "about itself",
                extra={"_fields": {"job_id": job.job_id, "subsystems": remedied}},
            )
        # Only alert for subsystems that survived dedup (a new incident, an
        # escalation, or a backoff-elapsed heartbeat) — an unrelated ongoing
        # incident's suppression must never swallow a different, new incident.
        if to_alert and self._alert is not None:
            alert_names = {s.name for s in to_alert}
            filtered_down = [s for s in down if s.name in alert_names]
            filtered_degraded = [s for s in degraded if s.name in alert_names]
            try:
                await self._alert(_compose_alert(filtered_down, filtered_degraded))
            except Exception as exc:  # alert failure must not fail the sweep itself
                log.scheduler.error(
                    "[scheduler] health_sweep.execute: alert sink raised",
                    exc_info=exc,
                    extra={"_fields": {"job_id": job.job_id}},
                )

        # 4. EXIT — a sweep that *found* a problem still ran successfully; the job
        # succeeded at its detection task (down count is metadata, not a job error).
        _log_exit(
            job, verdict="unhealthy", total=len(statuses), duration_ms=duration_ms,
            down=len(down), degraded=len(degraded),
        )
        return JobResult(
            job_id=job.job_id,
            effect_class="delivery",
            success=True,
            output=message,
            error=None,
            duration_ms=duration_ms,
            metadata={
                "down": len(down),
                "degraded": len(degraded),
                "total": len(statuses),
            },
        )

    async def _heal_and_verify(
        self,
        job: Job,
        down: Sequence[HealthStatus],
        degraded: Sequence[HealthStatus],
    ) -> dict[str, str]:
        """ADR-6 heal step: recycle every unhealthy subsystem that has a registered
        HealableResource. Returns ``{subsystem: heal_attempts.id}`` for every
        subsystem a recycle was ATTEMPTED for (the caller re-collects to confirm
        which actually recovered, and resolves each row to healed/exhausted —
        Story 2.6). No-op — empty dict — when the flag is OFF or no healer
        matches, keeping the sweep byte-identical.

        A subsystem stays in the returned mapping even when ``ensure_available()``
        itself raises: the ATTEMPT happened (recorded as ``heal.attempted``
        below, matching AD-5's "an attempt was made" framing), and the caller's
        re-verify is what decides healed vs. exhausted — an attempt that raised
        is exactly the exhausted case, not a silently dropped one. Never raises
        out: a heal error is logged and the subsystem simply stays unhealthy.
        """
        if not self._healers or not _health_loop_enabled():
            return {}
        from stackowl.pipeline.recovery_actuator import Failure, RecoveryActuator

        actuator = self._recovery or RecoveryActuator()
        attempted: dict[str, str] = {}
        for s in (*down, *degraded):
            healer = self._healers.get(s.name)
            if healer is None:
                continue
            # Route the retry DECISION through the ONE ADR-2 authority (a health
            # outage is transient + non-consequential — recycling re-opens a handle,
            # never double-commits a side effect).
            if not actuator.should_retry(
                Failure(name=s.name, kind="health", transient=True, consequential=False)
            ):
                continue
            row_id = await self._record_heal_attempted(s.name)
            attempted[s.name] = row_id
            try:
                await healer.ensure_available()
            except Exception as exc:  # a heal failure leaves it unhealthy → escalates
                log.scheduler.error(
                    "[scheduler] health_sweep.heal: recycle failed",
                    exc_info=exc,
                    extra={"_fields": {"job_id": job.job_id, "subsystem": s.name}},
                )
        return attempted

    async def _record_heal_attempted(self, subsystem: str) -> str:
        """Insert one ``heal_attempts`` row and its ``heal.attempted`` journal
        event, in the SAME transaction (AD-24). Returns the row id so the
        caller's later resolve (healed/exhausted) can address it."""
        row_id = str(uuid.uuid4())
        now_iso = datetime.now(UTC).isoformat()
        async with self._db.transaction() as conn:
            await conn.execute(
                "INSERT INTO heal_attempts "
                "(id, subsystem, status, attempt_count, created_at, updated_at) "
                "VALUES (?, ?, 'attempted', 1, ?, ?)",
                (row_id, subsystem, now_iso, now_iso),
            )
            await journal_record(conn, JournalEvent(
                type="heal.attempted",
                schema_version=1,
                actor_kind=ActorKind.AUTONOMOUS,
                actor_id=_ACTOR_ID,
                target_kind=ActorKind.OWNER,
                target_id=subsystem,
                outcome=Outcome.PENDING,
                record_ref=RecordRef(
                    kind="sqlite", locator={"table": "heal_attempts", "id": row_id},
                ),
                attrs=HealAttemptedAttrs(attempt_count=1),
            ))
        return row_id

    async def _resolve_heal_attempts(
        self, attempted: dict[str, str], healed: set[str], exhausted: set[str],
    ) -> None:
        """Resolve every attempted ``heal_attempts`` row to healed or exhausted,
        each in its own transaction alongside its journal event (AD-24)."""
        now_iso = datetime.now(UTC).isoformat()
        for name in healed:
            async with self._db.transaction() as conn:
                await conn.execute(
                    "UPDATE heal_attempts SET status = 'healed', updated_at = ? WHERE id = ?",
                    (now_iso, attempted[name]),
                )
                await journal_record(conn, JournalEvent(
                    type="heal.healed",
                    schema_version=1,
                    actor_kind=ActorKind.AUTONOMOUS,
                    actor_id=_ACTOR_ID,
                    target_kind=ActorKind.OWNER,
                    target_id=name,
                    outcome=Outcome.HEALED,
                    record_ref=RecordRef(
                        kind="sqlite", locator={"table": "heal_attempts", "id": attempted[name]},
                    ),
                    attrs=HealHealedAttrs(attempt_count=1),
                ))
        for name in exhausted:
            async with self._db.transaction() as conn:
                await conn.execute(
                    "UPDATE heal_attempts SET status = 'exhausted', updated_at = ? WHERE id = ?",
                    (now_iso, attempted[name]),
                )
                await journal_record(conn, JournalEvent(
                    type="heal.exhausted",
                    schema_version=1,
                    actor_kind=ActorKind.AUTONOMOUS,
                    actor_id=_ACTOR_ID,
                    target_kind=ActorKind.OWNER,
                    target_id=name,
                    # AD-5 — the heal loop's own give-up: NEEDS_YOU/HIGH, computed
                    # by the registry from this type, never set here.
                    outcome=Outcome.FAILED,
                    record_ref=RecordRef(
                        kind="sqlite", locator={"table": "heal_attempts", "id": attempted[name]},
                    ),
                    attrs=HealExhaustedAttrs(attempt_count=1),
                ))

    async def _record_health_changes(
        self, conn: aiosqlite.Connection, statuses: Sequence[HealthStatus],
    ) -> None:
        """FR84 — for each collected status, compare against the LAST row
        recorded for that subsystem in ``health_status_changes`` (no new
        in-memory cache) and journal a real transition.

        A subsystem with no prior row gets a silent SEED row (previous ==
        new, no journal event) so a FUTURE tick has a baseline to compare
        against — the table's own ``previous_status NOT NULL`` leaves no other
        way to represent "nothing observed yet". An unchanged tick records
        neither a row nor an event. Runs inside the CALLER's open transaction
        (AD-24) — never opens or commits its own.
        """
        for s in statuses:
            cursor = await conn.execute(
                "SELECT new_status FROM health_status_changes "
                "WHERE subsystem = ? ORDER BY occurred_at DESC LIMIT 1",
                (s.name,),
            )
            row = await cursor.fetchone()
            now_iso = datetime.now(UTC).isoformat()
            if row is None:
                # First-ever observation — nothing to compare against (I/O
                # matrix): seed the baseline, no journal event.
                await conn.execute(
                    "INSERT INTO health_status_changes "
                    "(id, subsystem, previous_status, new_status, error_code, occurred_at) "
                    "VALUES (?, ?, ?, ?, NULL, ?)",
                    (str(uuid.uuid4()), s.name, s.status, s.status, now_iso),
                )
                continue
            previous_status = str(row[0])
            if previous_status == s.status:
                continue  # unchanged tick — no row, no event
            # Never str(exc)/exception text (FR84, AD-4) — a closed code only,
            # and only when the NEW status is itself a failure/warning; a
            # transition back to a healthy state carries no error code.
            error_code = (
                classify_health_error(s.message) if s.status not in HEALTHY_STATES else None
            )
            row_id = str(uuid.uuid4())
            await conn.execute(
                "INSERT INTO health_status_changes "
                "(id, subsystem, previous_status, new_status, error_code, occurred_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (row_id, s.name, previous_status, s.status,
                 error_code.value if error_code else None, now_iso),
            )
            await journal_record(conn, JournalEvent(
                type="health.changed",
                schema_version=1,
                actor_kind=ActorKind.AUTONOMOUS,
                actor_id=_ACTOR_ID,
                target_kind=ActorKind.OWNER,
                target_id=s.name,
                outcome=Outcome.HEALED if s.status in HEALTHY_STATES else Outcome.FAILED,
                record_ref=RecordRef(
                    kind="sqlite", locator={"table": "health_status_changes", "id": row_id},
                ),
                attrs=HealthChangedAttrs(
                    previous_status=previous_status,
                    new_status=s.status,
                    error_code=error_code.value if error_code else None,
                ),
            ))

    def _dedupe_and_update(
        self, down: Sequence[HealthStatus], degraded: Sequence[HealthStatus]
    ) -> tuple[list[HealthStatus], list[str]]:
        """De-dupe/backoff the alert-sink send against an ONGOING incident.

        A status LEVEL change (e.g. degraded -> down) always bypasses backoff
        and alerts immediately; the SAME status only re-alerts once
        ``realert_backoff_s`` has elapsed since the last alert for it (a
        heartbeat re-alert, not a flood every tick). Never suppresses the
        caller's operator log — only the outbound alert-sink send.

        Returns ``(to_alert, resolved)``: subsystems to alert on THIS tick, and
        the names of previously-tracked subsystems no longer unhealthy (state
        for those is cleared here).
        """
        current = {s.name: s for s in (*down, *degraded)}
        to_alert: list[HealthStatus] = []
        for name, s in current.items():
            prior = self._alert_state.get(name)
            if prior is None or prior[0] != s.status:
                # New incident, or a level change (e.g. degraded -> down) —
                # bypass backoff and alert immediately.
                to_alert.append(s)
                self._alert_state[name] = (s.status, self._clock.monotonic())
            elif self._clock.monotonic() - prior[1] >= self._realert_backoff_s:
                # Same ongoing incident, backoff elapsed — heartbeat re-alert.
                to_alert.append(s)
                self._alert_state[name] = (s.status, self._clock.monotonic())
            else:
                continue
            if self._alert_record is not None:
                self._alert_record.record_alert(name, s.status)

        resolved = [name for name in self._alert_state if name not in current]
        for name in resolved:
            del self._alert_state[name]
        return to_alert, resolved

    async def _seed_alert_state(self) -> None:
        """Load what the operator was already paged about, once per process.

        Seeds the in-memory dedup map so a restart does not read as a new
        incident. Ages are converted onto the monotonic basis the map already
        uses, so the comparison below is unchanged. Never raises: a health sweep
        that cannot read its own history must still run, and failing to seed only
        restores the previous (noisier) behaviour rather than silencing anything.
        """
        if self._state_loaded or self._alert_record is None:
            return
        self._state_loaded = True
        try:
            recent = await self._alert_record.load_recent_alerts(self._realert_backoff_s)
        except Exception as exc:  # noqa: BLE001 — never cost the sweep its run
            log.scheduler.warning(
                "[scheduler] health_sweep: could not read the alert history — "
                "falling back to per-process dedup for this process",
                exc_info=exc,
            )
            return
        now = self._clock.monotonic()
        for name, (status, age_s) in recent.items():
            self._alert_state.setdefault(name, (status, now - float(age_s)))
        if recent:
            log.scheduler.info(
                "[scheduler] health_sweep: alert history restored",
                extra={"_fields": {"subsystems": sorted(recent), "n": len(recent)}},
            )

    @staticmethod
    def _compose_resolved(names: list[str]) -> str:
        """Human-readable operator notice for subsystems that recovered."""
        parts: list[str] = ["✅ recovered:"]
        for name in names:
            parts.append(f"  {name}")
        return "\n".join(parts)

    async def _maybe_send_resolved(self, resolved: list[str]) -> None:
        """Best-effort recovery notice; no-op when nothing recovered or unwired."""
        if not resolved or self._alert is None:
            return
        try:
            await self._alert(self._compose_resolved(resolved))
        except Exception as exc:  # alert failure must not fail the sweep itself
            log.scheduler.error(
                "[scheduler] health_sweep._maybe_send_resolved: alert sink raised",
                exc_info=exc,
                extra={"_fields": {"resolved": resolved}},
            )


def _compose_alert(
    down: Sequence[HealthStatus], degraded: Sequence[HealthStatus]
) -> str:
    """Human-readable operator alert summarising the unhealthy subsystems.

    CARRIES THE REMEDY WHEN THERE IS ONE (D14.4). This is the 2am surface: an alert that
    says only what broke makes the reader go and find out what to do, at the worst
    possible moment. A remedy the alert drops is a remedy that does not exist when it is
    needed most.

    The `→` clause is appended ONLY when a remedy is set. Most contributors have none —
    a growing prompt prefix has no command that fixes it — and a dangling arrow on every
    other line would teach the reader to stop seeing it.
    """
    parts: list[str] = ["⚠ StackOwl health sweep found unhealthy subsystems:"]
    # THE WORD COMES FROM THE STATUS, NOT FROM THE BUCKET IT LANDED IN. These two
    # lines used to hardcode "down" and "degraded", which was true only while the
    # vocabulary had exactly those two unhealthy words — the moment `unknown` joined
    # the warning bucket, the alert would have told an operator a subsystem was
    # DEGRADED when nothing had managed to measure it at all.
    for s in down:
        parts.append(f"  ✗ {s.name}: {s.status} — {s.message or 'no detail'}{_fix(s)}")
    for s in degraded:
        parts.append(f"  ⚠ {s.name}: {s.status} — {s.message or 'no detail'}{_fix(s)}")
    return "\n".join(parts)


def _fix(s: HealthStatus) -> str:
    """The remedy clause, or nothing at all when no action is known."""
    return f"\n      → {s.remedy}" if s.remedy else ""
