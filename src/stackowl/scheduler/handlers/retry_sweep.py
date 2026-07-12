"""RetrySweepHandler — periodically retries due retry_queue rows.

Mirrors the ClarifySweepHandler structure: a JobHandler subclass plus a
module-level register_retry_sweep_handler factory. The recurring JOB row is
seeded separately in scheduler/assembly.py (same place objective_driver is
seeded, every 1m).
"""

from __future__ import annotations

import time

from stackowl.infra.observability import log
from stackowl.memory.retry_queue_store import RetryQueueStore
from stackowl.pipeline.retry_actuator import RetryActuator
from stackowl.scheduler.base import HandlerRegistry, JobHandler
from stackowl.scheduler.job import Job, JobResult


class RetrySweepHandler(JobHandler):
    """Recurring sweep of due retry_queue rows — retries each via RetryActuator."""

    def __init__(self, *, actuator: RetryActuator, retry_store: RetryQueueStore) -> None:
        self._actuator = actuator
        self._retry_store = retry_store

    @property
    def handler_name(self) -> str:
        return "retry_sweep"

    async def execute(self, job: Job) -> JobResult:
        t0 = time.monotonic()
        # 1. ENTRY
        log.scheduler.info(
            "[scheduler] retry_sweep.execute: entry",
            extra={"_fields": {"job_id": job.job_id}},
        )
        retried = 0
        errored = 0
        try:
            due = await self._retry_store.get_due()
        except Exception as exc:
            log.scheduler.error(
                "[scheduler] retry_sweep.execute: get_due failed — treating as empty",
                exc_info=exc, extra={"_fields": {"job_id": job.job_id}},
            )
            due = []

        for row in due:
            try:
                await self._actuator.attempt_retry(row)
                retried += 1
            except Exception as exc:  # self-healing — one bad row must not stop the sweep
                errored += 1
                log.scheduler.error(
                    "[scheduler] retry_sweep.execute: attempt_retry raised for row",
                    exc_info=exc, extra={"_fields": {"job_id": job.job_id, "retry_id": row.id}},
                )

        duration_ms = (time.monotonic() - t0) * 1000
        # 4. EXIT
        log.scheduler.info(
            "[scheduler] retry_sweep.execute: exit",
            extra={"_fields": {
                "job_id": job.job_id, "retried": retried, "errored": errored,
                "duration_ms": duration_ms,
            }},
        )
        return JobResult(
            job_id=job.job_id, success=True,
            output=f"retried={retried} errored={errored}", error=None,
            duration_ms=duration_ms, effect_class="state_change",
        )


def register_retry_sweep_handler(
    *, actuator: RetryActuator, retry_store: RetryQueueStore,
) -> RetrySweepHandler:
    """Construct and register the RetrySweepHandler singleton.

    Mirrors register_clarify_sweep_handler. The recurring JOB row itself is
    seeded separately in scheduler/assembly.py.
    """
    handler = RetrySweepHandler(actuator=actuator, retry_store=retry_store)
    HandlerRegistry.instance().register(handler)
    log.scheduler.info(
        "[scheduler] retry_sweep handler registered",
        extra={"_fields": {"handler": handler.handler_name}},
    )
    return handler
