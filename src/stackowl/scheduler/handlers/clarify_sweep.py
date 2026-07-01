"""ClarifySweepHandler — periodically reap expired pending-clarify entries.

Blocking-await clarify entries self-reap via their own :func:`asyncio.wait_for`
timeout, but TURN-YIELD entries have no parked coroutine and no timer — an
abandoned turn-yield clarify would linger in the gateway's in-memory registry
forever. This handler drives :meth:`ClarifyGateway.sweep_expired` on a recurring
schedule so those entries are dropped once they age past ``ttl_seconds`` (default
1800s / 30min, matching the blocking-park timeout). The sweep is bounded, cheap,
and side-effect-free (in-memory only), so — like ``browser_cache_eviction`` — it
does NOT assert against test mode.

Mirrors the ``browser_cache_eviction`` handler structure: a :class:`JobHandler`
subclass plus a module-level :func:`register_clarify_sweep_handler` factory that
registers it on the process :class:`HandlerRegistry`. The recurring JOB row (the
``jobs`` table entry that makes the scheduler actually dispatch this handler on an
interval) is seeded SEPARATELY in the scheduler assembly — see this module's
docstring note and the report accompanying FF-E5-B3.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from stackowl.infra.observability import log
from stackowl.interaction.clarify_gateway import CLARIFY_TTL_SECONDS
from stackowl.scheduler.base import HandlerRegistry, JobHandler
from stackowl.scheduler.job import Job, JobResult

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.interaction.clarify_gateway import ClarifyGateway

# Default TTL for the sweep — the SINGLE SOURCE OF TRUTH shared with the
# blocking-park timeout (clarify.py) so a turn-yield entry is reaped on the same
# horizon a blocking one self-reaps.
_DEFAULT_TTL_SECONDS = CLARIFY_TTL_SECONDS


class ClarifySweepHandler(JobHandler):
    """Recurring sweep of expired pending-clarify entries.

    Holds a reference to the :class:`ClarifyGateway` singleton and a TTL. Each
    :meth:`execute` call drives :meth:`ClarifyGateway.sweep_expired` once and
    reports the number of entries dropped. ``sweep_expired`` already never
    raises; this handler guards anyway so a misbehaving gateway can never crash
    the scheduler loop (self-healing).
    """

    def __init__(
        self, gateway: ClarifyGateway, *, ttl_seconds: float = _DEFAULT_TTL_SECONDS,
    ) -> None:
        self._gateway = gateway
        self._ttl = ttl_seconds

    @property
    def handler_name(self) -> str:
        return "clarify_sweep"

    async def execute(self, job: Job) -> JobResult:
        t0 = time.monotonic()
        # 1. ENTRY
        log.scheduler.info(
            "[scheduler] clarify_sweep.execute: entry",
            extra={"_fields": {"job_id": job.job_id, "ttl_seconds": self._ttl}},
        )
        dropped = 0
        try:
            # 3. STEP — drive the gateway sweep (never raises, but guard anyway).
            dropped = self._gateway.sweep_expired(self._ttl)
        except Exception as exc:  # self-healing — never raise into the scheduler loop
            log.scheduler.error(
                "[scheduler] clarify_sweep.execute: sweep failed — treating as 0 dropped",
                exc_info=exc,
                extra={"_fields": {"job_id": job.job_id, "ttl_seconds": self._ttl}},
            )
        duration_ms = (time.monotonic() - t0) * 1000
        # 4. EXIT
        log.scheduler.info(
            "[scheduler] clarify_sweep.execute: exit",
            extra={"_fields": {
                "job_id": job.job_id,
                "dropped": dropped,
                "duration_ms": duration_ms,
            }},
        )
        return JobResult(
            job_id=job.job_id,
            effect_class="state_change",
            success=True,
            output=f"dropped={dropped}",
            error=None,
            duration_ms=duration_ms,
            metadata={"dropped": dropped, "ttl_seconds": self._ttl},
        )


def register_clarify_sweep_handler(
    gateway: ClarifyGateway, *, ttl_seconds: float = _DEFAULT_TTL_SECONDS,
) -> ClarifySweepHandler:
    """Construct and register the :class:`ClarifySweepHandler` singleton.

    Mirrors :func:`register_browser_cache_eviction_handler`: registers the
    handler on the process :class:`HandlerRegistry` so the scheduler can dispatch
    a ``clarify_sweep`` job to it. The recurring JOB row itself is seeded
    separately in the scheduler assembly (the same place ``_seed_minutes_schedule``
    seeds the other minute-scale recurring handlers).
    """
    handler = ClarifySweepHandler(gateway, ttl_seconds=ttl_seconds)
    HandlerRegistry.instance().register(handler)
    log.scheduler.info(
        "[scheduler] clarify_sweep handler registered",
        extra={"_fields": {"handler": handler.handler_name, "ttl_seconds": ttl_seconds}},
    )
    return handler
