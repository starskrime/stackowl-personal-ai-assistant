"""KnowledgePruneHandler — the platform's DECAY pass.

Runs the committed-facts pruner on a schedule (typically weekly). Wraps
:meth:`MemoryPruner.prune` in the scheduler contract so the operator
``/agents`` surface and lifecycle controls work uniformly across all
background agents.

ADR-19 — also runs :class:`SkillCurator`, because skills rot exactly the way
facts do and for the same reason: the platform is much better at CREATING
knowledge than at noticing which of it still earns its place. Measured
2026-08-05: 421 skills, 33 ever executed, 0 ever retired.

Both passes live in one job on purpose. Decay is a single concern, an operator
should have one thing to pause, and a skill pass that silently never ran would
be the exact failure mode ADR-19 exists to end. A curator failure is logged and
does NOT fail the job — pruning facts and pruning skills are independent, and
one must not mask the other.
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
    from stackowl.skills.lifecycle import SkillCurator


class KnowledgePruneHandler(JobHandler):
    """Wraps :class:`MemoryPruner` as a :class:`JobHandler`."""

    def __init__(self, pruner: MemoryPruner, curator: SkillCurator | None = None) -> None:
        self._pruner = pruner
        # Optional so every existing construction site keeps working unchanged;
        # when absent the skill pass is simply skipped (and says so).
        self._curator = curator

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
                effect_class="state_change",
                success=False,
                output=None,
                error=str(exc),
                duration_ms=duration_ms,
            )
        # 3. STEP — ADR-19 skill decay. Isolated: a curator failure must never
        # discard a successful fact prune, and vice versa.
        curated = await self._run_curator(job)

        duration_ms = (time.monotonic() - t0) * 1000
        # 4. EXIT
        log.scheduler.info(
            "[scheduler] knowledge_prune.execute: exit",
            extra={
                "_fields": {
                    "job_id": job.job_id,
                    "pruned": report.pruned_count,
                    "kept": report.kept_count,
                    "skills_curated": curated,
                    "duration_ms": duration_ms,
                }
            },
        )
        return JobResult(
            job_id=job.job_id,
            effect_class="state_change",
            success=not report.errors,
            output=(
                f"pruned={report.pruned_count} kept={report.kept_count}"
                f" skills_curated={curated}"
            ),
            error="; ".join(report.errors) if report.errors else None,
            duration_ms=duration_ms,
            metadata={
                "pruned_count": report.pruned_count,
                "kept_count": report.kept_count,
                "skills_curated": curated,
            },
        )

    async def _run_curator(self, job: Job) -> int:
        """Run the skill decay pass. Never raises — returns how many moved."""
        if self._curator is None:
            log.scheduler.debug(
                "[scheduler] knowledge_prune: no skill curator wired — skipping",
                extra={"_fields": {"job_id": job.job_id}},
            )
            return 0
        try:
            report = await self._curator.run()
        except Exception as exc:
            # Logged, not raised: the fact prune above already succeeded and
            # must not be reported as a failure because skill decay tripped.
            log.scheduler.error(
                "[scheduler] knowledge_prune: skill curator raised — fact prune stands",
                exc_info=exc,
                extra={"_fields": {"job_id": job.job_id}},
            )
            return 0
        return report.changed
