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
from collections.abc import Awaitable, Callable, Sequence
from typing import TYPE_CHECKING

from stackowl.infra.observability import log
from stackowl.scheduler.base import JobHandler
from stackowl.scheduler.job import Job, JobResult

if TYPE_CHECKING:
    from stackowl.health.aggregator import HealthAggregator
    from stackowl.health.status import HealthStatus

# An operator-alert sink: receives an already-composed alert message. Async.
AlertSink = Callable[[str], Awaitable[None]]


class HealthSweepHandler(JobHandler):
    """Runs :meth:`HealthAggregator.collect` and alerts on unhealthy subsystems."""

    def __init__(
        self,
        aggregator: HealthAggregator,
        *,
        alert: AlertSink | None = None,
    ) -> None:
        self._aggregator = aggregator
        self._alert = alert

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
                success=False,
                output=None,
                error=str(exc),
                duration_ms=duration_ms,
            )

        down = [s for s in statuses if s.status == "down"]
        degraded = [s for s in statuses if s.status == "degraded"]
        duration_ms = (time.monotonic() - t0) * 1000

        # 2. DECISION — all healthy → quiet exit; unhealthy → LOUD log + alert.
        if not down and not degraded:
            log.scheduler.debug(
                "[scheduler] health_sweep.execute: all healthy",
                extra={"_fields": {"job_id": job.job_id, "total": len(statuses)}},
            )
            return JobResult(
                job_id=job.job_id,
                success=True,
                output=f"healthy={len(statuses)}",
                error=None,
                duration_ms=duration_ms,
                metadata={"down": 0, "degraded": 0, "total": len(statuses)},
            )

        message = _compose_alert(down, degraded)
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
        if self._alert is not None:
            try:
                await self._alert(message)
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
