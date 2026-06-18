"""BrowserSessionRecycleHandler — scheduled belt-and-suspenders for the FF leak.

Camoufox/Firefox issue #245: RSS grows multi-GB over long-running goto() loops.
The runtime self-recycles on nav-count and idle thresholds, but a hard
scheduled tick (default hourly) ensures recycle still happens even when
traffic is low (e.g., overnight) and gives operators a single audit-emitting
event to track.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from stackowl.config.test_mode import TestModeGuard
from stackowl.infra.observability import log
from stackowl.scheduler.base import HandlerRegistry, JobHandler
from stackowl.scheduler.job import Job, JobResult

if TYPE_CHECKING:
    from stackowl.tools.browser.runtime import CamoufoxRuntime
    from stackowl.tools.browser.sessions import BrowserSessionRegistry


class BrowserSessionRecycleHandler(JobHandler):
    """Forces a runtime recycle pass and evicts idle browser sessions."""

    def __init__(self, runtime: CamoufoxRuntime, sessions: BrowserSessionRegistry) -> None:
        self._runtime = runtime
        self._sessions = sessions

    @property
    def handler_name(self) -> str:
        return "browser_recycle"

    async def execute(self, job: Job) -> JobResult:
        t0 = time.monotonic()
        log.scheduler.info(
            "[scheduler] browser_recycle.execute: entry",
            extra={"_fields": {"job_id": job.job_id}},
        )
        TestModeGuard.assert_not_test_mode("browser_recycle.execute")
        evicted = 0
        recycled = False
        if not self._runtime.available:
            log.scheduler.info(
                "[scheduler] browser_recycle.execute: runtime unavailable — skipping",
                extra={"_fields": {"job_id": job.job_id}},
            )
        else:
            try:
                evicted = await self._sessions.evict_idle()
            except Exception as exc:
                log.scheduler.warning(
                    "[scheduler] browser_recycle: evict_idle failed",
                    exc_info=exc,
                    extra={"_fields": {"job_id": job.job_id}},
                )
            try:
                await self._runtime.recycle_if_needed()
                recycled = True
            except Exception as exc:
                log.scheduler.error(
                    "[scheduler] browser_recycle: recycle_if_needed failed",
                    exc_info=exc,
                    extra={"_fields": {"job_id": job.job_id}},
                )
        duration_ms = (time.monotonic() - t0) * 1000
        log.scheduler.info(
            "[scheduler] browser_recycle.execute: exit",
            extra={"_fields": {
                "job_id": job.job_id,
                "evicted": evicted,
                "recycle_check_ok": recycled,
                "duration_ms": duration_ms,
            }},
        )
        return JobResult(
            job_id=job.job_id,
            success=True,
            output=f"evicted={evicted} recycle_check_ok={recycled}",
            error=None,
            duration_ms=duration_ms,
            metadata={"evicted_sessions": evicted, "recycle_check_ok": recycled},
        )


def register_browser_recycle_handler(
    runtime: CamoufoxRuntime, sessions: BrowserSessionRegistry,
) -> None:
    handler = BrowserSessionRecycleHandler(runtime=runtime, sessions=sessions)
    HandlerRegistry.instance().register(handler)
    log.scheduler.info(
        "[scheduler] browser_recycle handler registered",
        extra={"_fields": {"handler": handler.handler_name}},
    )
