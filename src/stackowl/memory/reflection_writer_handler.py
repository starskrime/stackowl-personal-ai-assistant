"""ReflectionWriterHandler — async job that generates Reflexion-style reflections.

Polls ``task_outcomes`` for rows that have been critic-scored AND meet the
reflection trigger criteria (failure_class IS NOT NULL OR quality_score <
0.6), runs a fast-tier LLM reflection call, embeds the summary, persists to
``reflections``. Mirrors :class:`CriticScorerHandler` exactly — same handler
contract, same 4-point logging, same JobResult shape.

Skip rule: rows that already have a reflection (LEFT JOIN reflections IS
NULL filter in :meth:`ReflectionStore.list_pending`).
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, ClassVar

from stackowl.config.test_mode import TestModeGuard
from stackowl.db.pool import DbPool
from stackowl.embeddings.registry import EmbeddingRegistry
from stackowl.infra.observability import log
from stackowl.memory.outcome_store import TaskOutcome
from stackowl.memory.reflection_prompt import (
    ReflectionPromptBuilder,
    parse_reflection_response,
)
from stackowl.memory.reflection_store import ReflectionStore
from stackowl.providers.base import ModelProvider
from stackowl.providers.registry import ProviderRegistry
from stackowl.scheduler.base import JobHandler
from stackowl.scheduler.job import Job, JobResult

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.learning.lessons_index import LessonsIndex

_REFLECTION_HANDLER_NAME = "reflection_writer"
_DEFAULT_BATCH_LIMIT = 10


class ReflectionWriterHandler(JobHandler):
    """Generate reflections for failed / low-quality outcomes."""

    _handler_name: ClassVar[str] = _REFLECTION_HANDLER_NAME

    def __init__(
        self,
        db: DbPool,
        provider_registry: ProviderRegistry,
        embedding_registry: EmbeddingRegistry,
        *,
        batch_limit: int = _DEFAULT_BATCH_LIMIT,
        critic_tier: str = "fast",
        lessons_index: LessonsIndex | None = None,
    ) -> None:
        self._db = db
        self._providers = provider_registry
        self._embeddings = embedding_registry
        self._store = ReflectionStore(db)
        self._prompt_builder = ReflectionPromptBuilder()
        self._batch_limit = batch_limit
        self._critic_tier = critic_tier
        # Learning Commit 5 — publish reflections into the cross-source
        # LessonsIndex so tools/parliament/classify can find them via one
        # ANN call. None for tests/dry-run.
        self._lessons_index = lessons_index
        log.memory.debug(
            "[reflection] handler.init: ready",
            extra={"_fields": {"tier": critic_tier, "batch_limit": batch_limit}},
        )

    @property
    def handler_name(self) -> str:
        return self._handler_name

    @property
    def defer_under_load(self) -> bool:
        return True  # Phase L — LLM reflection pass; yield to live user turns

    async def execute(self, job: Job) -> JobResult:
        # 1. ENTRY
        log.memory.debug(
            "[reflection] execute: entry",
            extra={"_fields": {"job_id": job.job_id}},
        )
        TestModeGuard.assert_not_test_mode("reflection_writer.execute")
        t0 = time.monotonic()

        # 2. DECISION — fetch pending eligible outcomes
        try:
            pending = await self._store.list_pending(limit=self._batch_limit)
        except Exception as exc:  # B5
            log.memory.error(
                "[reflection] execute: list_pending failed",
                exc_info=exc,
                extra={"_fields": {"job_id": job.job_id}},
            )
            duration_ms = (time.monotonic() - t0) * 1000
            return JobResult(
                job_id=job.job_id, success=False, output=None,
                error=str(exc), duration_ms=duration_ms,
                metadata={"written": 0},
            )

        if not pending:
            duration_ms = (time.monotonic() - t0) * 1000
            log.memory.debug(
                "[reflection] execute: exit — no pending outcomes",
                extra={"_fields": {"job_id": job.job_id, "duration_ms": duration_ms}},
            )
            return JobResult(
                job_id=job.job_id, success=True, output="written:0",
                error=None, duration_ms=duration_ms,
                metadata={"written": 0, "pending_count": 0},
            )

        log.memory.info(
            "[reflection] execute: reflecting batch",
            extra={"_fields": {"job_id": job.job_id, "pending_count": len(pending)}},
        )

        # 3. STEP — pick a provider, reflect on each pending row
        try:
            provider: ModelProvider = self._providers.get_with_cascade(self._critic_tier)
        except Exception as exc:  # B5
            log.memory.error(
                "[reflection] execute: no provider for reflection",
                exc_info=exc,
                extra={"_fields": {"job_id": job.job_id, "tier": self._critic_tier}},
            )
            duration_ms = (time.monotonic() - t0) * 1000
            return JobResult(
                job_id=job.job_id, success=False, output=None,
                error=f"no provider for tier {self._critic_tier}: {exc}",
                duration_ms=duration_ms,
                metadata={"written": 0, "pending_count": len(pending)},
            )

        written = 0
        for outcome in pending:
            ok = await self._reflect_one(outcome, provider)
            if ok:
                written += 1

        duration_ms = (time.monotonic() - t0) * 1000
        # 4. EXIT
        log.memory.info(
            "[reflection] execute: exit",
            extra={"_fields": {
                "job_id": job.job_id, "written": written,
                "pending_count": len(pending), "duration_ms": duration_ms,
            }},
        )
        return JobResult(
            job_id=job.job_id, success=True, output=f"written:{written}",
            error=None, duration_ms=duration_ms,
            metadata={"written": written, "pending_count": len(pending)},
        )

    async def _reflect_one(
        self, outcome: TaskOutcome, provider: ModelProvider,
    ) -> bool:
        """Run one reflection call + write the row. Returns success."""
        # 1. ENTRY
        log.memory.debug(
            "[reflection] reflect_one: entry",
            extra={"_fields": {
                "outcome_id": outcome.outcome_id,
                "trace_id": outcome.trace_id,
                "failure_class": outcome.failure_class,
                "quality_score": outcome.quality_score,
            }},
        )
        # 2. DECISION — build prompt
        messages = self._prompt_builder.build(outcome)
        # 3. STEP — provider call
        try:
            result = await provider.complete(messages, model="")
        except Exception as exc:  # B5
            log.memory.warning(
                "[reflection] reflect_one: provider.complete failed — skipping",
                exc_info=exc,
                extra={"_fields": {"outcome_id": outcome.outcome_id}},
            )
            return False
        # 2. DECISION (cont.) — parse
        parsed = parse_reflection_response(result.content)
        if parsed is None:
            log.memory.warning(
                "[reflection] reflect_one: could not parse response — skipping",
                extra={"_fields": {
                    "outcome_id": outcome.outcome_id,
                    "raw_preview": result.content[:200],
                }},
            )
            return False
        summary, suggested_strategy = parsed

        # 3. STEP (cont.) — embed + write
        embedding, embedding_model = await self._embed(summary)
        try:
            await self._store.write(
                trace_id=outcome.trace_id,
                owl_name=outcome.owl_name,
                summary=summary,
                suggested_strategy=suggested_strategy,
                failure_class=outcome.failure_class,
                quality_score=outcome.quality_score,
                embedding=embedding,
                embedding_model=embedding_model,
            )
        except Exception as exc:  # B5
            log.memory.warning(
                "[reflection] reflect_one: store.write failed — skipping",
                exc_info=exc,
                extra={"_fields": {"outcome_id": outcome.outcome_id}},
            )
            return False
        # Publish to LessonsIndex so tools/parliament can semantically find
        # this reflection (Learning Commit 5). Best-effort — failures don't
        # block the reflection itself.
        await self._publish_to_lessons(
            outcome=outcome,
            summary=summary, suggested_strategy=suggested_strategy,
        )
        # 4. EXIT
        log.memory.debug(
            "[reflection] reflect_one: exit",
            extra={"_fields": {
                "outcome_id": outcome.outcome_id,
                "summary_len": len(summary),
            }},
        )
        return True

    async def _publish_to_lessons(
        self, *, outcome: TaskOutcome, summary: str, suggested_strategy: str,
    ) -> None:
        """Best-effort: push the reflection into the cross-source LessonsIndex."""
        if self._lessons_index is None:
            return
        from stackowl.learning.lessons_index import LessonDraft

        # Compose the lesson content — same shape the LLM gets at retrieval.
        content = (
            f"Reflection on {outcome.owl_name} task ({outcome.failure_class or 'low-quality'}): "
            f"{summary}"
        )
        if suggested_strategy:
            content += f" Strategy: {suggested_strategy}"
        try:
            await self._lessons_index.publish(LessonDraft(
                source_type="reflection",
                source_ref=outcome.trace_id,
                content=content,
                metadata={
                    "owl_name": outcome.owl_name,
                    "failure_class": outcome.failure_class or "",
                    "quality_score": outcome.quality_score or 0.0,
                    "tool_sequence": ",".join(outcome.tool_sequence),
                },
            ))
        except Exception as exc:  # B5 — lessons are enhancement, not gating
            log.memory.warning(
                "[reflection] reflect_one: lessons_index.publish failed — skipping",
                exc_info=exc,
                extra={"_fields": {"trace_id": outcome.trace_id}},
            )

    async def _embed(self, text: str) -> tuple[list[float] | None, str | None]:
        """Best-effort embedding via the wired EmbeddingRegistry.

        Returns ``(None, None)`` on any failure — reflection is still useful
        without an embedding (we can fall back to recent_for_owl retrieval).
        """
        # 1. ENTRY
        log.memory.debug(
            "[reflection] _embed: entry",
            extra={"_fields": {"text_len": len(text)}},
        )
        # 3. STEP — call the wired embedding provider
        try:
            provider = self._embeddings.get()
            embeddings = await provider.embed([text])
        except Exception as exc:  # B5
            log.memory.warning(
                "[reflection] _embed: failed — storing without vector",
                exc_info=exc,
            )
            return None, None
        # 2. DECISION — provider returned an empty list
        if not embeddings:
            log.memory.debug(
                "[reflection] _embed: exit — empty embeddings list",
            )
            return None, None
        try:
            model_name = provider.model_name
        except Exception:
            model_name = None
        # 4. EXIT
        log.memory.debug(
            "[reflection] _embed: exit",
            extra={"_fields": {"dim": len(embeddings[0]), "model": model_name}},
        )
        return list(embeddings[0]), model_name
