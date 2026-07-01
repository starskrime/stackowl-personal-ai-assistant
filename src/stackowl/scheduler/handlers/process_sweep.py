"""ProcessSweepHandler — periodically drive the ProcessRegistry sweep (E9-S0).

A tracked process in the :class:`stackowl.process.registry.ProcessRegistry` has no
self-reaping timer. Without a recurring sweep an abandoned process could outlive
its MANDATORY maximum lifetime, a dead handle could linger forever, and chatty
processes could collectively exceed the aggregate capture ceiling. This handler
drives :meth:`ProcessRegistry.sweep` on a recurring schedule so:

* a process still running past its ``ttl_deadline`` is AUTO-KILLED (the mandatory
  TTL rail), and
* dead handles past the prune TTL are dropped, and
* the aggregate-buffer ceiling is enforced (oldest captures evicted first).

Mirrors :class:`stackowl.scheduler.handlers.session_sweep.SessionSweepHandler`: a
:class:`JobHandler` subclass plus a :func:`register_process_sweep_handler` factory
that registers it on the process :class:`HandlerRegistry`. The recurring JOB row
(the ``jobs`` entry the scheduler dispatches on an interval) is seeded SEPARATELY
in the scheduler assembly (``every 10m``). The sweep is bounded, in-memory + a
cheap checkpoint write, so — like ``session_sweep`` — it does NOT assert test mode.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from stackowl.infra.observability import log
from stackowl.scheduler.base import HandlerRegistry, JobHandler
from stackowl.scheduler.job import Job, JobResult

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.process.registry import ProcessRegistry


class ProcessSweepHandler(JobHandler):
    """Recurring sweep of TTL-overdue / dead / over-ceiling tracked processes.

    Holds the :class:`ProcessRegistry` singleton; each :meth:`execute` drives
    :meth:`ProcessRegistry.sweep` once and reports the action counts.
    ``ProcessRegistry.sweep`` already heals internally; this handler guards anyway
    so a misbehaving registry can never crash the scheduler loop (self-healing).
    """

    def __init__(self, registry: ProcessRegistry) -> None:
        self._registry = registry

    @property
    def handler_name(self) -> str:
        return "process_sweep"

    async def execute(self, job: Job) -> JobResult:
        t0 = time.monotonic()
        # 1. ENTRY
        log.scheduler.info(
            "[scheduler] process_sweep.execute: entry",
            extra={"_fields": {"job_id": job.job_id}},
        )
        counts: dict[str, int] = {"auto_killed": 0, "pruned": 0, "evicted": 0}
        try:
            # 3. STEP — drive the registry sweep (heals internally; guard anyway).
            counts = await self._registry.sweep()
        except Exception as exc:  # self-healing — never raise into the scheduler loop
            log.scheduler.error(
                "[scheduler] process_sweep.execute: sweep failed — treating as no-op",
                exc_info=exc,
                extra={"_fields": {"job_id": job.job_id}},
            )
        duration_ms = (time.monotonic() - t0) * 1000
        # 4. EXIT
        log.scheduler.info(
            "[scheduler] process_sweep.execute: exit",
            extra={"_fields": {"job_id": job.job_id, **counts, "duration_ms": duration_ms}},
        )
        return JobResult(
            job_id=job.job_id,
            effect_class="state_change",
            success=True,
            output=(
                f"auto_killed={counts['auto_killed']} "
                f"pruned={counts['pruned']} evicted={counts['evicted']}"
            ),
            error=None,
            duration_ms=duration_ms,
            metadata=dict(counts),
        )


def register_process_sweep_handler(registry: ProcessRegistry) -> ProcessSweepHandler:
    """Construct and register the :class:`ProcessSweepHandler` singleton.

    Mirrors :func:`register_session_sweep_handler`: registers the handler on the
    process :class:`HandlerRegistry` so the scheduler can dispatch a
    ``process_sweep`` job to it. The recurring JOB row itself is seeded separately
    in the scheduler assembly (alongside the other minute-scale recurring handlers).
    """
    handler = ProcessSweepHandler(registry)
    HandlerRegistry.instance().register(handler)
    log.scheduler.info(
        "[scheduler] process_sweep handler registered",
        extra={"_fields": {"handler": handler.handler_name}},
    )
    return handler
