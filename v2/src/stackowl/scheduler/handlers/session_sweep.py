"""SessionSweepHandler — periodically reap idle named owl sessions (E8-S3).

A named session in the :class:`stackowl.owls.session_registry.SessionRegistry`
has no self-reaping timer (unlike a blocking-await clarify entry). An abandoned
session would otherwise linger — and its A2A mailbox with it — forever. This
handler drives :meth:`SessionRegistry.sweep` on a recurring schedule so a session
idle past ``SESSION_IDLE_TTL_SECONDS`` is dropped AND its mailbox drained. The
sweep is bounded, cheap, and in-memory only, so — like ``clarify_sweep`` /
``browser_cache_eviction`` — it does NOT assert against test mode.

Mirrors :class:`stackowl.scheduler.handlers.clarify_sweep.ClarifySweepHandler`: a
:class:`JobHandler` subclass plus a :func:`register_session_sweep_handler` factory
that registers it on the process :class:`HandlerRegistry`. The recurring JOB row
(the ``jobs`` entry that makes the scheduler dispatch this on an interval) is
seeded SEPARATELY in the scheduler assembly (``every 10m``), alongside the other
minute-scale recurring handlers.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from stackowl.infra.observability import log
from stackowl.scheduler.base import HandlerRegistry, JobHandler
from stackowl.scheduler.job import Job, JobResult

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.owls.session_registry import SessionRegistry


class SessionSweepHandler(JobHandler):
    """Recurring sweep of idle-past-TTL named owl sessions.

    Holds the :class:`SessionRegistry` singleton; each :meth:`execute` drives
    :meth:`SessionRegistry.sweep` once and reports the reap count.
    ``SessionRegistry.sweep`` already never raises; this handler guards anyway so a
    misbehaving registry can never crash the scheduler loop (self-healing).
    """

    def __init__(self, registry: SessionRegistry) -> None:
        self._registry = registry

    @property
    def handler_name(self) -> str:
        return "session_sweep"

    async def execute(self, job: Job) -> JobResult:
        t0 = time.monotonic()
        # 1. ENTRY
        log.scheduler.info(
            "[scheduler] session_sweep.execute: entry",
            extra={"_fields": {"job_id": job.job_id}},
        )
        reaped = 0
        try:
            # 3. STEP — drive the registry sweep (never raises, but guard anyway).
            reaped = self._registry.sweep()
        except Exception as exc:  # self-healing — never raise into the scheduler loop
            log.scheduler.error(
                "[scheduler] session_sweep.execute: sweep failed — treating as 0 reaped",
                exc_info=exc,
                extra={"_fields": {"job_id": job.job_id}},
            )
        duration_ms = (time.monotonic() - t0) * 1000
        # 4. EXIT
        log.scheduler.info(
            "[scheduler] session_sweep.execute: exit",
            extra={"_fields": {"job_id": job.job_id, "reaped": reaped, "duration_ms": duration_ms}},
        )
        return JobResult(
            job_id=job.job_id,
            success=True,
            output=f"reaped={reaped}",
            error=None,
            duration_ms=duration_ms,
            metadata={"reaped": reaped},
        )


def register_session_sweep_handler(registry: SessionRegistry) -> SessionSweepHandler:
    """Construct and register the :class:`SessionSweepHandler` singleton.

    Mirrors :func:`register_clarify_sweep_handler`: registers the handler on the
    process :class:`HandlerRegistry` so the scheduler can dispatch a
    ``session_sweep`` job to it. The recurring JOB row itself is seeded separately
    in the scheduler assembly (the place ``_seed_minutes_schedule`` seeds the other
    minute-scale recurring handlers).
    """
    handler = SessionSweepHandler(registry)
    HandlerRegistry.instance().register(handler)
    log.scheduler.info(
        "[scheduler] session_sweep handler registered",
        extra={"_fields": {"handler": handler.handler_name}},
    )
    return handler
