"""KnowledgePruneHandler — proxies to :class:`MemoryPruner`.

Runs the committed-facts pruner on a schedule (typically weekly). Wraps
:meth:`MemoryPruner.prune` in the scheduler contract so the operator
``/agents`` surface and lifecycle controls work uniformly across all
background agents.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from stackowl.config.test_mode import TestModeGuard
from stackowl.infra.observability import log
from stackowl.scheduler.base import JobHandler
from stackowl.scheduler.job import Job, JobResult

if TYPE_CHECKING:
    from stackowl.memory.pruner import MemoryPruner


class KnowledgePruneHandler(JobHandler):
    """Wraps :class:`MemoryPruner` as a :class:`JobHandler`."""

    def __init__(self, pruner: MemoryPruner) -> None:
        self._pruner = pruner

    @property
    def handler_name(self) -> str:
        return "knowledge_prune"

    async def execute(self, job: Job) -> JobResult:
        # 1. ENTRY
        log.scheduler.debug(
            "[scheduler] knowledge_prune.execute: entry",
            extra={"_fields": {"job_id": job.job_id}},
        )
        TestModeGuard.assert_not_test_mode("knowledge_prune.execute")
        t0 = time.monotonic()
        # 2. DECISION
        log.scheduler.debug(
            "[scheduler] knowledge_prune.execute: delegating to MemoryPruner",
            extra={"_fields": {"job_id": job.job_id}},
        )
        try:
            # 3. STEP
            report = await self._pruner.prune()
        except Exception as exc:
            duration_ms = (time.monotonic() - t0) * 1000
            log.scheduler.error(
                "[scheduler] knowledge_prune.execute: pruner raised",
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
        duration_ms = (time.monotonic() - t0) * 1000
        # 4. EXIT
        log.scheduler.info(
            "[scheduler] knowledge_prune.execute: exit",
            extra={
                "_fields": {
                    "job_id": job.job_id,
                    "pruned": report.pruned_count,
                    "kept": report.kept_count,
                    "duration_ms": duration_ms,
                }
            },
        )
        return JobResult(
            job_id=job.job_id,
            success=not report.errors,
            output=f"pruned={report.pruned_count} kept={report.kept_count}",
            error="; ".join(report.errors) if report.errors else None,
            duration_ms=duration_ms,
            metadata={
                "pruned_count": report.pruned_count,
                "kept_count": report.kept_count,
            },
        )
