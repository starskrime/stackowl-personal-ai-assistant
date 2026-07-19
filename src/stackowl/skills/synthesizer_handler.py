"""SkillSynthesizerHandler — scheduler-driven daily skill synthesis.

Mirrors :class:`CriticScorerHandler` (Commit 1) and
:class:`ReflectionWriterHandler` (Commit 2). Daily cadence per operator vote;
seeded by :class:`SchedulerAssembly` in 3c.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from stackowl.config.test_mode import TestModeGuard
from stackowl.db.pool import DbPool
from stackowl.infra.observability import log
from stackowl.memory.outcome_store import TaskOutcomeStore
from stackowl.providers.registry import ProviderRegistry
from stackowl.scheduler.base import JobHandler
from stackowl.scheduler.job import Job, JobResult
from stackowl.skills.store import SkillIndexStore
from stackowl.skills.synthesizer import SkillSynthesizer

if TYPE_CHECKING:  # pragma: no cover — typing-only imports
    from stackowl.embeddings.registry import EmbeddingRegistry
    from stackowl.memory.kuzu_adapter import KuzuAdapter
    from stackowl.owls.registry import OwlRegistry
    from stackowl.tools.registry import ConsequentialActionGate

_HANDLER_NAME = "skill_synthesizer"


class SkillSynthesizerHandler(JobHandler):
    """Run the discover / refine / deprecate loop once per dispatch."""

    _handler_name: ClassVar[str] = _HANDLER_NAME

    def __init__(
        self,
        db: DbPool,
        provider_registry: ProviderRegistry,
        skill_store: SkillIndexStore,
        skills_root: Path,
        *,
        embedding_registry: EmbeddingRegistry | None = None,
        owl_registry: OwlRegistry | None = None,
        consent_gate: ConsequentialActionGate | None = None,
        kuzu: KuzuAdapter | None = None,
        synth_tier: str = "fast",
        lookback_days: int = 14,
        min_cluster_size: int = 3,
        min_mean_quality: float = 0.75,
    ) -> None:
        self._db = db
        self._providers = provider_registry
        self._skill_store = skill_store
        self._skills_root = skills_root
        self._embedding_registry = embedding_registry
        self._owl_registry = owl_registry
        self._consent_gate = consent_gate
        self._kuzu = kuzu
        self._synth_tier = synth_tier
        self._lookback_days = lookback_days
        self._min_cluster_size = min_cluster_size
        self._min_mean_quality = min_mean_quality
        log.skills.debug(
            "[synth] handler.init: ready",
            extra={"_fields": {
                "tier": synth_tier, "lookback_days": lookback_days,
                "min_cluster_size": min_cluster_size,
            }},
        )

    @property
    def handler_name(self) -> str:
        return self._handler_name

    async def execute(self, job: Job) -> JobResult:
        # 1. ENTRY
        log.skills.debug(
            "[synth] handler.execute: entry",
            extra={"_fields": {"job_id": job.job_id}},
        )
        TestModeGuard.assert_not_test_mode("skill_synthesizer.execute")
        t0 = time.monotonic()

        # 2. DECISION — pick a provider
        try:
            provider = self._providers.get_with_cascade(self._synth_tier)
        except Exception as exc:  # B5
            log.skills.error(
                "[synth] handler.execute: no provider for synthesis",
                exc_info=exc,
                extra={"_fields": {"job_id": job.job_id, "tier": self._synth_tier}},
            )
            duration_ms = (time.monotonic() - t0) * 1000
            return JobResult(
                job_id=job.job_id,
                effect_class="state_change", success=False, output=None,
                error=f"no provider for tier {self._synth_tier}: {exc}",
                duration_ms=duration_ms,
                metadata={"created": 0, "refined": 0, "deprecated": 0},
            )

        # 3. STEP — run all three phases via the synthesizer
        synth = SkillSynthesizer(
            outcome_store=TaskOutcomeStore(self._db),
            skill_store=self._skill_store,
            provider=provider,
            skills_root=self._skills_root,
            embedding_registry=self._embedding_registry,
            owl_registry=self._owl_registry,
            db=self._db,
            consent_gate=self._consent_gate,
            kuzu=self._kuzu,
            lookback_days=self._lookback_days,
            min_cluster_size=self._min_cluster_size,
            min_mean_quality=self._min_mean_quality,
        )
        report = await synth.run_all()
        duration_ms = (time.monotonic() - t0) * 1000

        # 4. EXIT
        log.skills.info(
            "[synth] handler.execute: exit",
            extra={"_fields": {
                "job_id": job.job_id,
                "created": report.created, "refined": report.refined,
                "deprecated": report.deprecated, "duration_ms": duration_ms,
            }},
        )
        return JobResult(
            job_id=job.job_id,
            effect_class="state_change", success=True,
            output=f"created:{report.created} refined:{report.refined} "
                   f"deprecated:{report.deprecated}",
            error=None, duration_ms=duration_ms,
            metadata={
                "created": report.created, "refined": report.refined,
                "deprecated": report.deprecated,
            },
        )
