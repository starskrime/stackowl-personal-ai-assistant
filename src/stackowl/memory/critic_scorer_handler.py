"""CriticScorerHandler — async job that fills in quality_score for pending outcomes.

Polls ``task_outcomes WHERE quality_score IS NULL``, runs a fast-tier LLM
critic call on each pending row, writes the score back. Mirrors
:class:`stackowl.notifications.digest_job.NotificationDigestJob` — same
handler pattern, same 4-point logging, same JobResult shape.

FR-4 (learning-loop consolidation): this handler is no longer scheduled on
its own standing job/cadence. :class:`stackowl.memory.reflection_writer_handler.ReflectionWriterHandler`
composes an instance of this class and calls its :meth:`execute` at the start
of its own ``execute()``, so one scheduler job (reflection_writer, every 15m)
does both scoring and reflection. This class is kept as its own unit
(independently testable, still async-via-scheduler relative to the
user-facing pipeline) rather than inlined.
"""

from __future__ import annotations

import time
from typing import ClassVar

from stackowl.config.test_mode import TestModeGuard
from stackowl.db.pool import DbPool
from stackowl.infra.observability import log
from stackowl.memory.critic_prompt import CriticScorerPromptBuilder, parse_critic_response
from stackowl.memory.outcome_store import TaskOutcome, TaskOutcomeStore
from stackowl.providers.base import ModelProvider
from stackowl.providers.registry import ProviderRegistry
from stackowl.scheduler.base import JobHandler
from stackowl.scheduler.job import Job, JobResult

_CRITIC_HANDLER_NAME = "critic_scorer"
_DEFAULT_BATCH_LIMIT = 25


class CriticScorerHandler(JobHandler):
    """Score pending task_outcomes via a fast-tier LLM critic."""

    _handler_name: ClassVar[str] = _CRITIC_HANDLER_NAME

    def __init__(
        self,
        db: DbPool,
        provider_registry: ProviderRegistry,
        *,
        batch_limit: int = _DEFAULT_BATCH_LIMIT,
        critic_tier: str = "fast",
    ) -> None:
        self._db = db
        self._providers = provider_registry
        self._store = TaskOutcomeStore(db)
        self._prompt_builder = CriticScorerPromptBuilder()
        self._batch_limit = batch_limit
        self._critic_tier = critic_tier
        log.memory.debug(
            "[critic] handler.init: ready",
            extra={"_fields": {"tier": critic_tier, "batch_limit": batch_limit}},
        )

    @property
    def handler_name(self) -> str:
        return self._handler_name

    @property
    def defer_under_load(self) -> bool:
        return True  # Phase L — LLM scoring pass; yield to live user turns

    async def execute(self, job: Job) -> JobResult:
        # 1. ENTRY
        log.memory.debug(
            "[critic] execute: entry",
            extra={"_fields": {"job_id": job.job_id}},
        )
        TestModeGuard.assert_not_test_mode("critic_scorer.execute")
        t0 = time.monotonic()

        # 2. DECISION — fetch pending rows
        try:
            pending = await self._store.list_pending_critic(limit=self._batch_limit)
        except Exception as exc:  # B5 — never silent
            log.memory.error(
                "[critic] execute: list_pending_critic failed",
                exc_info=exc,
                extra={"_fields": {"job_id": job.job_id}},
            )
            duration_ms = (time.monotonic() - t0) * 1000
            return JobResult(
                job_id=job.job_id,
                effect_class="state_change", success=False, output=None,
                error=str(exc), duration_ms=duration_ms,
                metadata={"scored": 0},
            )

        if not pending:
            duration_ms = (time.monotonic() - t0) * 1000
            log.memory.debug(
                "[critic] execute: exit — no pending outcomes",
                extra={"_fields": {"job_id": job.job_id, "duration_ms": duration_ms}},
            )
            return JobResult(
                job_id=job.job_id,
                effect_class="state_change", success=True, output="scored:0",
                error=None, duration_ms=duration_ms,
                metadata={"scored": 0, "pending_count": 0},
            )

        log.memory.info(
            "[critic] execute: scoring batch",
            extra={"_fields": {"job_id": job.job_id, "pending_count": len(pending)}},
        )

        # 3. STEP — score each pending outcome
        try:
            provider, model = self._providers.get_with_cascade_and_model(self._critic_tier)
        except Exception as exc:  # B5
            log.memory.error(
                "[critic] execute: no provider available for critic",
                exc_info=exc,
                extra={"_fields": {"job_id": job.job_id, "tier": self._critic_tier}},
            )
            duration_ms = (time.monotonic() - t0) * 1000
            return JobResult(
                job_id=job.job_id,
                effect_class="state_change", success=False, output=None,
                error=f"no provider for tier {self._critic_tier}: {exc}",
                duration_ms=duration_ms,
                metadata={"scored": 0, "pending_count": len(pending)},
            )

        scored = 0
        for outcome in pending:
            score = await self._score_one(outcome, provider, model)
            if score is not None:
                try:
                    await self._store.set_quality_score(outcome.outcome_id, score)
                    scored += 1
                except Exception as exc:  # B5
                    log.memory.warning(
                        "[critic] execute: set_quality_score failed",
                        exc_info=exc,
                        extra={"_fields": {"outcome_id": outcome.outcome_id}},
                    )

        duration_ms = (time.monotonic() - t0) * 1000
        # 4. EXIT
        log.memory.info(
            "[critic] execute: exit",
            extra={"_fields": {
                "job_id": job.job_id,
                "scored": scored,
                "pending_count": len(pending),
                "duration_ms": duration_ms,
            }},
        )
        return JobResult(
            job_id=job.job_id,
            effect_class="state_change", success=True, output=f"scored:{scored}",
            error=None, duration_ms=duration_ms,
            metadata={"scored": scored, "pending_count": len(pending)},
        )

    async def _score_one(
        self, outcome: TaskOutcome, provider: ModelProvider, model: str,
    ) -> float | None:
        """Run one critic call. Returns the parsed score or None on any failure."""
        # 1. ENTRY
        log.memory.debug(
            "[critic] score_one: entry",
            extra={"_fields": {
                "outcome_id": outcome.outcome_id,
                "trace_id": outcome.trace_id,
                "owl_name": outcome.owl_name,
            }},
        )
        # 2. DECISION — build prompt
        messages = self._prompt_builder.build(outcome)
        # 3. STEP — provider call
        try:
            # disable_thinking: a JSON quality-score verdict needs no chain-of-
            # thought; a reasoning-capable provider otherwise burns the whole
            # max_tokens budget on <think> and never emits the score (same
            # empty-reply failure mode fixed in owls/router.py).
            result = await provider.complete(messages, model=model, disable_thinking=True)
        except Exception as exc:  # B5
            log.memory.warning(
                "[critic] score_one: provider.complete failed — skipping",
                exc_info=exc,
                extra={"_fields": {"outcome_id": outcome.outcome_id}},
            )
            return None
        # 2. DECISION (cont.) — parse
        score = parse_critic_response(result.content)
        # 4. EXIT
        if score is None:
            log.memory.warning(
                "[critic] score_one: could not parse critic response — skipping",
                extra={"_fields": {
                    "outcome_id": outcome.outcome_id,
                    "raw_preview": result.content[:200],
                }},
            )
        else:
            log.memory.debug(
                "[critic] score_one: exit",
                extra={"_fields": {"outcome_id": outcome.outcome_id, "score": score}},
            )
        return score
