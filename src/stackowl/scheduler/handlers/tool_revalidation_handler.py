"""ToolRevalidationHandler — daily scheduler wrapper around
:func:`revalidate_learned_tools` (FX-09).

Mirrors :class:`ToolOutcomeMinerHandler`/:class:`SkillSynthesizerHandler` — same
JobHandler contract, same 4-point logging, same JobResult shape.

Before this handler existed, ``revalidate_learned_tools`` was invoked only from
a manual CLI command (``stackowl db revalidate-tools``) — a learned tool with
zero trustworthy successes stayed live and reloaded on every boot until an
operator ran it by hand. This closes that gap: the same quarantine pass now
runs automatically, daily, after the outcome miner has had a chance to
populate fresh trust counts.
"""

from __future__ import annotations

import time
from typing import ClassVar

from stackowl.config.test_mode import TestModeGuard
from stackowl.db.pool import DbPool
from stackowl.infra.observability import log
from stackowl.scheduler.base import JobHandler
from stackowl.scheduler.job import Job, JobResult
from stackowl.tools.meta.tool_revalidation import revalidate_learned_tools

_HANDLER_NAME = "tool_revalidation"


class ToolRevalidationHandler(JobHandler):
    """Daily quarantine pass over learned tools with no trustworthy successes."""

    _handler_name: ClassVar[str] = _HANDLER_NAME

    def __init__(self, db: DbPool, *, min_evidence: int = 3) -> None:
        self._db = db
        self._min_evidence = min_evidence
        log.tool.debug(
            "[tool_revalidation] handler.init: ready",
            extra={"_fields": {"min_evidence": min_evidence}},
        )

    @property
    def handler_name(self) -> str:
        return self._handler_name

    async def execute(self, job: Job) -> JobResult:
        # 1. ENTRY
        log.tool.debug(
            "[tool_revalidation] handler.execute: entry",
            extra={"_fields": {"job_id": job.job_id}},
        )
        TestModeGuard.assert_not_test_mode("tool_revalidation.execute")
        t0 = time.monotonic()

        # 3. STEP — run the same pass the manual CLI command uses.
        try:
            report = await revalidate_learned_tools(self._db, min_evidence=self._min_evidence)
        except Exception as exc:  # B5 — a bad file must never abort the scheduled sweep
            duration_ms = (time.monotonic() - t0) * 1000
            log.tool.error(
                "[tool_revalidation] handler.execute: revalidation crashed",
                exc_info=exc,
                extra={"_fields": {"job_id": job.job_id, "duration_ms": duration_ms}},
            )
            return JobResult(
                job_id=job.job_id,
                effect_class="state_change", success=False, output=None,
                error=str(exc), duration_ms=duration_ms,
                metadata={"evicted": 0, "kept": 0},
            )

        duration_ms = (time.monotonic() - t0) * 1000
        # 4. EXIT
        log.tool.info(
            "[tool_revalidation] handler.execute: exit",
            extra={"_fields": {
                "job_id": job.job_id,
                "suspects": len(report.suspects),
                "evicted": len(report.evicted),
                "kept": len(report.kept),
                "insufficient_evidence": len(report.insufficient_evidence),
                "no_history": len(report.no_history),
                "duration_ms": duration_ms,
            }},
        )
        return JobResult(
            job_id=job.job_id,
            effect_class="state_change", success=True,
            output=(
                f"evicted={len(report.evicted)} kept={len(report.kept)} "
                f"insufficient_evidence={len(report.insufficient_evidence)}"
            ),
            error=None, duration_ms=duration_ms,
            metadata={
                "suspects": report.suspects,
                "evicted": report.evicted,
                "kept": len(report.kept),
                "insufficient_evidence": len(report.insufficient_evidence),
                "no_history": len(report.no_history),
            },
        )
