"""CheckInHandler — stub for the scheduled wellbeing/check-in agent.

Full implementation lands in Story 7.3; this handler exists today so the
:class:`HandlerRegistry` can advertise ``check_in`` and the boundary
scripts treat the handler surface as complete.
"""

from __future__ import annotations

import time

from stackowl.config.test_mode import TestModeGuard
from stackowl.infra.observability import log
from stackowl.scheduler.base import JobHandler
from stackowl.scheduler.job import Job, JobResult


class CheckInHandler(JobHandler):
    """No-op check-in handler — returns a success result."""

    @property
    def handler_name(self) -> str:
        return "check_in"

    async def execute(self, job: Job) -> JobResult:
        # 1. ENTRY
        log.scheduler.debug(
            "[scheduler] check_in.execute: entry",
            extra={"_fields": {"job_id": job.job_id, "schedule": job.schedule}},
        )
        TestModeGuard.assert_not_test_mode("check_in.execute")
        t0 = time.monotonic()
        # 2. DECISION
        log.scheduler.debug(
            "[scheduler] check_in.execute: stub noop (Story 7.3 implements)",
            extra={"_fields": {"job_id": job.job_id}},
        )
        # 3. STEP — nothing to do yet
        duration_ms = (time.monotonic() - t0) * 1000
        # 4. EXIT
        log.scheduler.info(
            "[scheduler] check_in.execute: exit",
            extra={"_fields": {"job_id": job.job_id, "duration_ms": duration_ms}},
        )
        return JobResult(
            job_id=job.job_id,
            success=True,
            output="check_in: noop",
            error=None,
            duration_ms=duration_ms,
        )
