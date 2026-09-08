"""KnowledgePruneHandler — the platform's DECAY pass.

Runs the SKILL curator on a schedule (typically weekly).

WHAT IT USED TO ALSO DO. This handler wrapped MemoryPruner, which pruned
committed facts by age and confidence. That store was emptied and its writers
retired in D08.1, so the pruner had nothing left to prune and went with them.
The skill decay pass — the ADR-19 half — is now the whole job.

The old wrapper text, kept for context: wraps the prune step
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

AND THE AUDIT LEG, ADDED 2026-09-08 — BUILT, TESTED, AND NEVER RUN UNTIL NOW.
``audit/retention.py`` has had a careful ``AuditRetention.prune()`` all along: it
lifts the no-delete trigger, deletes past the cutoff, restores the trigger inside
one EXCLUSIVE transaction, holds DELETION records to their own longer horizon, and
appends a prune record. MEASURED 2026-09-08 it was constructed in exactly ONE
place — ``tests/test_story_12_4.py`` — and NOWHERE in ``src/``. The
``audit_retention_days`` setting was the mirror image: declared, documented, and
read by nothing. Two halves of one feature, each complete, neither joined to the
other, so ``audit_log`` reached 11,353 rows spanning ~105 days against a
advertised 90-day bound. That is CLAUDE.md's failure shape 5 with both halves
present, which is the version that looks most like working software.

It belongs in THIS job rather than a new one for the reason the paragraph above
already gives: decay is a single concern and the operator should have one thing to
pause. The handler used to prune by age and lost that leg when the fact store was
retired; this restores the shape rather than inventing a second engine.

Bakir set the horizon on 2026-09-08: "Audit can be deleted after 14 days. Fix
code." DELETION records keep their own 365-day retention, untouched — they are the
record OF deletions and outliving the rows they describe is their whole point.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from stackowl.config.test_mode import TestModeGuard
from stackowl.infra.observability import log
from stackowl.scheduler.base import JobHandler
from stackowl.scheduler.job import Job, JobResult

if TYPE_CHECKING:
    from stackowl.audit.retention import AuditRetention
    from stackowl.skills.lifecycle import SkillCurator


class KnowledgePruneHandler(JobHandler):
    """Wraps :class:`SkillCurator` as a :class:`JobHandler`."""

    def __init__(
        self,
        curator: SkillCurator | None = None,
        audit_retention: AuditRetention | None = None,
    ) -> None:
        # Optional so every existing construction site keeps working unchanged;
        # when absent the pass is simply skipped (and says so). The audit leg
        # follows the curator's shape exactly rather than inventing a second one.
        self._curator = curator
        self._audit_retention = audit_retention

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
        # 3. STEP — ADR-19 skill decay, and the audit leg beside it.
        curated = await self._run_curator(job)
        audit_pruned = await self._run_audit_retention(job)

        duration_ms = (time.monotonic() - t0) * 1000
        # 4. EXIT
        log.scheduler.info(
            "[scheduler] knowledge_prune.execute: exit",
            extra={
                "_fields": {
                    "job_id": job.job_id,
                    "skills_curated": curated,
                    "audit_rows_pruned": audit_pruned,
                    "duration_ms": duration_ms,
                }
            },
        )
        return JobResult(
            job_id=job.job_id,
            effect_class="state_change",
            success=True,
            output=f"skills_curated={curated} audit_rows_pruned={audit_pruned}",
            error=None,
            duration_ms=duration_ms,
            metadata={"skills_curated": curated, "audit_rows_pruned": audit_pruned},
        )

    async def _run_audit_retention(self, job: Job) -> int:
        """Prune audit rows past their horizon. Never raises — returns how many went.

        Mirrors ``_run_curator`` deliberately: a failure here is logged and does NOT
        fail the job, because the two decay legs are independent and one must not mask
        the other. `prune()` is synchronous sqlite and brief, so it runs inline.
        """
        if self._audit_retention is None:
            log.scheduler.debug(
                "[scheduler] knowledge_prune: no audit retention wired — skipping",
                extra={"_fields": {"job_id": job.job_id}},
            )
            return 0
        try:
            pruned = self._audit_retention.prune()
        except Exception as exc:
            # Every except logs. A decay failure must never take the job with it.
            log.scheduler.error(
                "[scheduler] knowledge_prune: audit retention failed — job continues",
                exc_info=exc,
                extra={"_fields": {"job_id": job.job_id}},
            )
            return 0
        if pruned:
            log.scheduler.info(
                "[scheduler] knowledge_prune: audit rows pruned past their horizon",
                extra={"_fields": {"job_id": job.job_id, "rows": pruned}},
            )
        return int(pruned)

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
            # Logged, not raised: skill decay is the whole job now (D08.1), but a
            # curator that trips is still a degraded pass rather than a failed
            # one — the job reports 0 curated instead of erroring the schedule.
            log.scheduler.error(
                "[scheduler] knowledge_prune: skill curator raised — fact prune stands",
                exc_info=exc,
                extra={"_fields": {"job_id": job.job_id}},
            )
            return 0
        return report.changed
