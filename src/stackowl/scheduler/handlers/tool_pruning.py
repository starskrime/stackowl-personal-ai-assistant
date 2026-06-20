"""ToolPruningHandler — stub for the periodic unused-tool scrubber.

A future story (Epic 8) prunes unused plugin tools from the registry on
a low-frequency schedule. The handler is staged now so the scheduler
schema and operator commands can reference ``tool_pruning`` immediately.
"""

from __future__ import annotations

import time

from stackowl.config.test_mode import TestModeGuard
from stackowl.infra.observability import log
from stackowl.scheduler.base import JobHandler, TriggerKind
from stackowl.scheduler.job import Job, JobResult


class ToolPruningHandler(JobHandler):
    """No-op tool-pruning handler — returns a success result."""

    @property
    def handler_name(self) -> str:
        return "tool_pruning"

    @property
    def trigger_kind(self) -> TriggerKind:
        # Register-only stub (Epic 8 implements). SchedulerAssembly registers it
        # but seeds NO standing jobs row — it is enqueued on demand once the
        # pruner exists. Declares on_demand so the wiring audit does not flag the
        # (correctly) unseeded stub as a dangling never-fires handler.
        return "on_demand"

    async def execute(self, job: Job) -> JobResult:
        # 1. ENTRY
        log.scheduler.debug(
            "[scheduler] tool_pruning.execute: entry",
            extra={"_fields": {"job_id": job.job_id}},
        )
        TestModeGuard.assert_not_test_mode("tool_pruning.execute")
        t0 = time.monotonic()
        # 2. DECISION
        log.scheduler.debug(
            "[scheduler] tool_pruning.execute: stub noop (Epic 8 implements)",
            extra={"_fields": {"job_id": job.job_id}},
        )
        # 3. STEP — nothing yet
        duration_ms = (time.monotonic() - t0) * 1000
        # 4. EXIT
        log.scheduler.info(
            "[scheduler] tool_pruning.execute: exit",
            extra={"_fields": {"job_id": job.job_id, "duration_ms": duration_ms}},
        )
        return JobResult(
            job_id=job.job_id,
            success=True,
            output="tool_pruning: noop",
            error=None,
            duration_ms=duration_ms,
        )
