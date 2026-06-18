"""TurnSweepHandler — periodically drive the TurnRegistry backstop sweep (F050).

A turn left RUNNING after a missed ``_on_done`` (its task is ``done()`` but its
status never reached ``DONE``) wedges ``TurnRegistry._running[session_id]`` forever,
jamming ALL later same-session routing (silent per-session unresponsiveness —
"Jarvis goes deaf" for that chat). :meth:`TurnRegistry.sweep` is the backstop reaper
for exactly this wedge, but had no production caller. This handler drives it on a
recurring schedule so:

* a turn whose task is done but status never reached DONE is deregistered (freeing
  its ``_running`` slot), and
* a reaped-but-stranded session (queued intake, no running turn) is surfaced to the
  global-cap drain seam (the registry's ``on_stranded`` callback) so a reap is never
  fake-success.

A line-for-line clone of
:class:`stackowl.scheduler.handlers.process_sweep.ProcessSweepHandler`: a
:class:`JobHandler` subclass plus a :func:`register_turn_sweep_handler` factory.
The recurring JOB row is seeded SEPARATELY in the scheduler assembly (``every 10m``,
which stays well under the host-scaled TTL). The sweep is bounded + in-memory, so —
like ``process_sweep`` — it does NOT assert test mode. ``TurnRegistry.sweep`` already
heals internally; this handler guards anyway so a misbehaving registry can never
crash the scheduler loop (self-healing).
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from stackowl.gateway.turn_registry import default_turn_ttl_seconds
from stackowl.infra.observability import log
from stackowl.scheduler.base import HandlerRegistry, JobHandler
from stackowl.scheduler.job import Job, JobResult

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.gateway.turn_registry import TurnRegistry


class TurnSweepHandler(JobHandler):
    """Recurring backstop sweep of done-but-not-DONE wedged turns (F050).

    Holds the :class:`TurnRegistry` singleton plus a host-scaled ``ttl_seconds``
    backstop; each :meth:`execute` drives :meth:`TurnRegistry.sweep` once and
    reports the reaped count.
    """

    def __init__(self, registry: TurnRegistry, *, ttl_seconds: float) -> None:
        self._registry = registry
        self._ttl = ttl_seconds

    @property
    def handler_name(self) -> str:
        return "turn_sweep"

    async def execute(self, job: Job) -> JobResult:
        t0 = time.monotonic()
        # 1. ENTRY
        log.scheduler.info(
            "[scheduler] turn_sweep.execute: entry",
            extra={"_fields": {"job_id": job.job_id, "ttl_s": self._ttl}},
        )
        reaped: list[str] = []
        try:
            # 3. STEP — drive the registry sweep (heals internally; guard anyway).
            reaped = await self._registry.sweep(ttl_seconds=self._ttl)
        except Exception as exc:  # self-healing — never raise into the scheduler loop
            log.scheduler.error(
                "[scheduler] turn_sweep.execute: sweep failed — treating as no-op",
                exc_info=exc,
                extra={"_fields": {"job_id": job.job_id}},
            )
        duration_ms = (time.monotonic() - t0) * 1000
        # 4. EXIT
        log.scheduler.info(
            "[scheduler] turn_sweep.execute: exit",
            extra={"_fields": {"job_id": job.job_id, "reaped": len(reaped),
                               "duration_ms": duration_ms}},
        )
        return JobResult(
            job_id=job.job_id,
            success=True,
            output=f"reaped={len(reaped)}",
            error=None,
            duration_ms=duration_ms,
            metadata={"reaped": len(reaped)},
        )


def register_turn_sweep_handler(
    registry: TurnRegistry, *, ttl_seconds: float | None = None
) -> TurnSweepHandler:
    """Construct and register the :class:`TurnSweepHandler` singleton.

    Mirrors :func:`register_process_sweep_handler`: registers the handler on the
    shared :class:`HandlerRegistry` so the scheduler can dispatch a ``turn_sweep``
    job to it. The recurring JOB row itself is seeded separately in the scheduler
    assembly. ``ttl_seconds`` defaults to the host-scaled backstop.
    """
    ttl = default_turn_ttl_seconds() if ttl_seconds is None else ttl_seconds
    handler = TurnSweepHandler(registry, ttl_seconds=ttl)
    HandlerRegistry.instance().register(handler)
    log.scheduler.info(
        "[scheduler] turn_sweep handler registered",
        extra={"_fields": {"handler": handler.handler_name, "ttl_s": ttl}},
    )
    return handler
