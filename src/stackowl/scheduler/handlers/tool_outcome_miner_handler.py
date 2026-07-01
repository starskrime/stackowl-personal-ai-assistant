"""ToolOutcomeMinerHandler — daily scheduler wrapper around :class:`ToolOutcomeMiner`.

Mirrors :class:`CriticScorerHandler` (Commit 1) and
:class:`SkillSynthesizerHandler` (Commit 3) — same JobHandler contract,
same 4-point logging, same JobResult shape.
"""

from __future__ import annotations

import time
from typing import ClassVar

from stackowl.config.test_mode import TestModeGuard
from stackowl.db.pool import DbPool
from stackowl.infra.observability import log
from stackowl.learning.lessons_index import LessonsIndex
from stackowl.learning.tool_heuristic_store import ToolHeuristicStore
from stackowl.learning.tool_outcome_miner import ToolOutcomeMiner
from stackowl.memory.outcome_store import TaskOutcomeStore
from stackowl.scheduler.base import JobHandler
from stackowl.scheduler.job import Job, JobResult

_HANDLER_NAME = "tool_outcome_miner"


class ToolOutcomeMinerHandler(JobHandler):
    """Daily mining of (tool, condition → outcome) heuristics."""

    _handler_name: ClassVar[str] = _HANDLER_NAME

    def __init__(
        self,
        db: DbPool,
        lessons_index: LessonsIndex | None = None,
        *,
        lookback_days: int = 30,
        min_evidence: int = 3,
    ) -> None:
        self._db = db
        self._lessons_index = lessons_index
        self._lookback_days = lookback_days
        self._min_evidence = min_evidence
        log.memory.debug(
            "[heuristic] handler.init: ready",
            extra={"_fields": {
                "lookback_days": lookback_days,
                "min_evidence": min_evidence,
                "has_lessons_index": lessons_index is not None,
            }},
        )

    @property
    def handler_name(self) -> str:
        return self._handler_name

    async def execute(self, job: Job) -> JobResult:
        # 1. ENTRY
        log.memory.debug(
            "[heuristic] handler.execute: entry",
            extra={"_fields": {"job_id": job.job_id}},
        )
        TestModeGuard.assert_not_test_mode("tool_outcome_miner.execute")
        t0 = time.monotonic()

        # 3. STEP — wire deps + run miner
        miner = ToolOutcomeMiner(
            outcome_store=TaskOutcomeStore(self._db),
            heuristic_store=ToolHeuristicStore(self._db),
            lessons_index=self._lessons_index,
            lookback_days=self._lookback_days,
            min_evidence=self._min_evidence,
        )
        try:
            report = await miner.mine()
        except Exception as exc:  # B5
            duration_ms = (time.monotonic() - t0) * 1000
            log.memory.error(
                "[heuristic] handler.execute: mining crashed",
                exc_info=exc,
                extra={"_fields": {"job_id": job.job_id, "duration_ms": duration_ms}},
            )
            return JobResult(
                job_id=job.job_id,
                effect_class="state_change", success=False, output=None,
                error=str(exc), duration_ms=duration_ms,
                metadata={"n_heuristics_written": 0, "n_lessons_published": 0},
            )

        duration_ms = (time.monotonic() - t0) * 1000
        # 4. EXIT
        log.memory.info(
            "[heuristic] handler.execute: exit",
            extra={"_fields": {
                "job_id": job.job_id,
                "n_outcomes_scanned": report.n_outcomes_scanned,
                "n_heuristics_written": report.n_heuristics_written,
                "n_lessons_published": report.n_lessons_published,
                "duration_ms": duration_ms,
            }},
        )
        return JobResult(
            job_id=job.job_id,
            effect_class="state_change", success=True,
            output=(
                f"outcomes={report.n_outcomes_scanned} "
                f"heuristics={report.n_heuristics_written} "
                f"lessons={report.n_lessons_published}"
            ),
            error=None, duration_ms=duration_ms,
            metadata={
                "n_outcomes_scanned": report.n_outcomes_scanned,
                "n_heuristics_written": report.n_heuristics_written,
                "n_lessons_published": report.n_lessons_published,
            },
        )
