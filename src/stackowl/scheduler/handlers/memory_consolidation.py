"""MemoryConsolidationHandler — proxies to the DreamWorker job handler.

The dream-worker performs the actual consolidation work; this thin proxy
exposes the canonical ``memory_consolidation`` handler name (FR139) so
operator-facing tooling, /agents log output, and downstream notification
routing can address consolidation jobs by their semantic identifier
rather than by an implementation-specific name.
"""

from __future__ import annotations

import time

from stackowl.config.test_mode import TestModeGuard
from stackowl.infra.observability import log
from stackowl.scheduler.base import JobHandler
from stackowl.scheduler.job import Job, JobResult


class MemoryConsolidationHandler(JobHandler):
    """Forwards execution to a wrapped :class:`JobHandler` (the dream worker)."""

    def __init__(self, dream_worker: JobHandler) -> None:
        self._dream_worker = dream_worker

    @property
    def handler_name(self) -> str:
        return "memory_consolidation"

    async def execute(self, job: Job) -> JobResult:
        # 1. ENTRY
        log.scheduler.debug(
            "[scheduler] memory_consolidation.execute: entry",
            extra={"_fields": {"job_id": job.job_id}},
        )
        TestModeGuard.assert_not_test_mode("memory_consolidation.execute")
        t0 = time.monotonic()
        # 2. DECISION — delegate to the wrapped dream worker
        log.scheduler.debug(
            "[scheduler] memory_consolidation.execute: delegating to dream_worker",
            extra={"_fields": {"target": self._dream_worker.handler_name}},
        )
        # 3. STEP
        result = await self._dream_worker.execute(job)
        # 4. EXIT
        duration_ms = (time.monotonic() - t0) * 1000
        log.scheduler.info(
            "[scheduler] memory_consolidation.execute: exit",
            extra={
                "_fields": {
                    "job_id": job.job_id,
                    "success": result.success,
                    "duration_ms": duration_ms,
                }
            },
        )
        return result
