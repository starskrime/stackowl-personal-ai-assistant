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
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Protocol

from stackowl.infra.clock import Clock, WallClock
from stackowl.infra.observability import log
from stackowl.scheduler.base import JobHandler
from stackowl.scheduler.job import Job, JobResult


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
            if s.name.startswith("provider:") and s.status == "ok"
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


def _health_loop_enabled() -> bool:
    """ADR-6 flag read — module-level so tests can monkeypatch it. Never raises."""
    try:
        from stackowl.config.settings import Settings

        return bool(Settings().health_loop)
    except Exception:  # noqa: BLE001 — a flag read must never wedge the sweep
        return False


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

        down = [s for s in statuses if s.status == "down"]
        degraded = [s for s in statuses if s.status == "degraded"]
        duration_ms = (time.monotonic() - t0) * 1000

        # 2. DECISION — all healthy → quiet exit; unhealthy → LOUD log + alert.
        if not down and not degraded:
            _, resolved = self._dedupe_and_update([], [])
            await self._maybe_send_resolved(resolved)
            log.scheduler.debug(
                "[scheduler] health_sweep.execute: all healthy",
                extra={"_fields": {"job_id": job.job_id, "total": len(statuses)}},
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
            down = [s for s in statuses if s.status == "down"]
            degraded = [s for s in statuses if s.status == "degraded"]
            duration_ms = (time.monotonic() - t0) * 1000
            still_unhealthy = {s.name for s in (*down, *degraded)}
            healed = attempted - still_unhealthy  # recycled AND re-verified ok
            if healed:
                log.scheduler.warning(
                    "[scheduler] health_sweep.execute: subsystems RECOVERED after heal",
                    extra={"_fields": {"job_id": job.job_id, "healed": sorted(healed)}},
                )
            if not down and not degraded:
                # 4. EXIT — every unhealthy subsystem was healed + re-verified. No alert.
                _, resolved = self._dedupe_and_update([], [])
                await self._maybe_send_resolved(resolved)
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
                }
            },
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
    ) -> set[str]:
        """ADR-6 heal step: recycle every unhealthy subsystem that has a registered
        HealableResource. Returns the set of names a recycle was ATTEMPTED for (the
        caller re-collects to confirm which actually recovered). No-op — empty set —
        when the flag is OFF or no healer matches, keeping the sweep byte-identical.
        Never raises: a heal error is logged and the subsystem simply stays unhealthy.
        """
        if not self._healers or not _health_loop_enabled():
            return set()
        from stackowl.pipeline.recovery_actuator import Failure, RecoveryActuator

        actuator = self._recovery or RecoveryActuator()
        attempted: set[str] = set()
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
            try:
                await healer.ensure_available()
                attempted.add(s.name)
            except Exception as exc:  # a heal failure leaves it unhealthy → escalates
                log.scheduler.error(
                    "[scheduler] health_sweep.heal: recycle failed",
                    exc_info=exc,
                    extra={"_fields": {"job_id": job.job_id, "subsystem": s.name}},
                )
        return attempted

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
    """Human-readable operator alert summarising the unhealthy subsystems."""
    parts: list[str] = ["⚠ StackOwl health sweep found unhealthy subsystems:"]
    for s in down:
        parts.append(f"  ✗ {s.name}: down — {s.message or 'no detail'}")
    for s in degraded:
        parts.append(f"  ⚠ {s.name}: degraded — {s.message or 'no detail'}")
    return "\n".join(parts)
