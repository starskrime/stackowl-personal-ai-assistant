"""ReflectionWriterHandler — async job that scores + reflects on task outcomes.

FR-4 (learning-loop consolidation): this handler now does BOTH steps that used
to be two separate scheduler jobs on two separate cadences:

1. Score pending outcomes via the composed :class:`CriticScorerHandler` (was
   the standalone ``critic_scorer`` job, every 10m).
2. Generate Reflexion-style reflections for rows that have been critic-scored
   AND meet the POSITIVE-ONLY reflection trigger (success = 1 AND
   failure_class IS NULL AND quality_score >= 0.6), runs a fast-tier LLM
   reflection call, embeds the summary, persists to ``reflections``.

Because step 2's eligibility query requires ``quality_score`` to already be
set, reflection was previously DEPENDENT on critic_scorer having already run
on its own cadence — a fresh successful outcome could wait up to ~10m for
scoring, then another ~15m for reflection. Running both in one ``execute()``
closes that gap: a row scored in step 1 above is immediately visible to step
2's re-fetch in the SAME run.

Skip rule: rows that already have a reflection (LEFT JOIN reflections IS
NULL filter in :meth:`ReflectionStore.list_pending`).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from stackowl.config.test_mode import TestModeGuard
from stackowl.db.pool import DbPool
from stackowl.embeddings.registry import EmbeddingRegistry
from stackowl.infra.observability import log
from stackowl.memory.critic_scorer_handler import CriticScorerHandler
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

# LAT.4 — pending outcomes are persisted in bounded chunks of this size
# instead of one execute()-per-row autocommit (pool.py:27-38's documented
# starvation case). Bounded (not unbounded) so one background job can never
# itself hold the single-writer lock for a long unbroken span (AC #3).
CHUNK_SIZE = 50


@dataclass
class _PreparedReflection:
    """A reflection whose (slow, network-bound) LLM completion + embedding
    have already been computed — ready for a fast, lock-held DB write."""

    outcome: TaskOutcome
    summary: str
    suggested_strategy: str
    embedding: list[float] | None
    embedding_model: str | None


class ReflectionWriterHandler(JobHandler):
    """Score (FR-4) + generate reflections for SUCCESSFUL, high-quality outcomes."""

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
        critic: CriticScorerHandler | None = None,
        turn_registry: object | None = None,
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
        # FR-4 — composed, not deleted: CriticScorerHandler keeps its own
        # scoring logic/prompt builder/tests; this handler just calls its
        # execute() first so ONE scheduler job does both steps. Accept an
        # injected instance (DI/test seam) or build the default one.
        self._critic = critic if critic is not None else CriticScorerHandler(
            db=db, provider_registry=provider_registry, critic_tier=critic_tier,
        )
        # FR-4 gap fix — CriticScorerHandler.defer_under_load=True is now dead
        # code (it's no longer scheduled standalone, so the scheduler's own
        # deferral check never consults it). Preserve the original "critic
        # yields to live turns" behavior by consulting the same duck-typed
        # TurnRegistry the scheduler uses, directly, before invoking the
        # critic phase. None (untested/CLI/dry-run) ⇒ never skip, matching
        # the scheduler's own no-turn-registry-wired behavior.
        self._turn_registry = turn_registry
        log.memory.debug(
            "[reflection] handler.init: ready",
            extra={"_fields": {"tier": critic_tier, "batch_limit": batch_limit}},
        )

    @property
    def handler_name(self) -> str:
        return self._handler_name

    @property
    def defer_under_load(self) -> bool:
        # FR-5 — light, 15-min-cadence handler; the 900s starvation cap can
        # chronically slip it a full cycle on an active box. Never defer.
        return False

    async def execute(self, job: Job) -> JobResult:
        # 1. ENTRY
        log.memory.debug(
            "[reflection] execute: entry",
            extra={"_fields": {"job_id": job.job_id}},
        )
        TestModeGuard.assert_not_test_mode("reflection_writer.execute")
        t0 = time.monotonic()

        # 2. DECISION — FR-4: run the critic scoring pass FIRST so a row that
        # just became eligible (quality_score newly set below) is picked up by
        # THIS SAME run's reflection pass instead of waiting for a separate
        # cadence. Combine-error semantics: a critic-phase failure is logged
        # and folded into this job's error/metadata, but does NOT block the
        # reflection pass — already-scored rows can and should still be
        # reflected on (more resilient than an all-or-nothing job).
        #
        # Deferral: the critic's own LLM batch is heavy (up to 25 rows); skip
        # it under live load exactly as the standalone job used to (via
        # self._critic.defer_under_load), while reflection itself never
        # defers (FR-5). Skipped-this-tick rows stay unscored and are picked
        # up on the next run once load clears.
        scored = 0
        pending_count_critic = 0
        critic_error: str | None = None
        critic_success = True
        if self._critic.defer_under_load and self._turn_registry is not None \
                and self._turn_registry.has_active_turns():  # type: ignore[attr-defined]
            log.memory.debug(
                "[reflection] execute: critic phase deferred — live turns active",
                extra={"_fields": {"job_id": job.job_id}},
            )
        else:
            critic_result = await self._critic.execute(job)
            scored = int((critic_result.metadata or {}).get("scored", 0))
            pending_count_critic = int((critic_result.metadata or {}).get("pending_count", 0))
            critic_success = critic_result.success
            if not critic_result.success:
                critic_error = critic_result.error
                log.memory.warning(
                    "[reflection] execute: critic phase failed — continuing with reflection pass",
                    extra={"_fields": {"job_id": job.job_id, "critic_error": critic_error}},
                )

        # 3. DECISION — fetch pending eligible outcomes for reflection (now
        # also includes anything the critic pass above just scored).
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
                job_id=job.job_id,
                effect_class="state_change", success=False, output=None,
                error=critic_error or str(exc), duration_ms=duration_ms,
                metadata={
                    "scored": scored, "written": 0,
                    "pending_count_critic": pending_count_critic,
                    "pending_count_reflection": 0,
                    "critic_success": critic_success,
                },
            )

        if not pending:
            duration_ms = (time.monotonic() - t0) * 1000
            log.memory.debug(
                "[reflection] execute: exit — no pending outcomes",
                extra={"_fields": {"job_id": job.job_id, "duration_ms": duration_ms}},
            )
            # Success semantics match the exit block below: the reflection
            # phase (this fetch) completed cleanly, so success=True even when
            # the critic phase failed — a critic failure is surfaced via
            # `error`/`metadata`, never silently, but doesn't flip success
            # (this handler made real, non-vacuous progress on its own
            # fetch). Consistent with the full-batch exit path.
            return JobResult(
                job_id=job.job_id,
                effect_class="state_change",
                success=True, output=f"scored:{scored} written:0",
                error=critic_error, duration_ms=duration_ms,
                metadata={
                    "scored": scored, "written": 0,
                    "pending_count_critic": pending_count_critic,
                    "pending_count_reflection": 0,
                    "critic_success": critic_success,
                },
            )

        log.memory.info(
            "[reflection] execute: reflecting batch",
            extra={"_fields": {"job_id": job.job_id, "pending_count": len(pending)}},
        )

        # 4. STEP — pick a provider, reflect on each pending row
        try:
            provider, model = self._providers.get_with_cascade(self._critic_tier)
        except Exception as exc:  # B5
            log.memory.error(
                "[reflection] execute: no provider for reflection",
                exc_info=exc,
                extra={"_fields": {"job_id": job.job_id, "tier": self._critic_tier}},
            )
            duration_ms = (time.monotonic() - t0) * 1000
            return JobResult(
                job_id=job.job_id,
                effect_class="state_change", success=False, output=None,
                error=f"no provider for tier {self._critic_tier}: {exc}",
                duration_ms=duration_ms,
                metadata={
                    "scored": scored, "written": 0,
                    "pending_count_critic": pending_count_critic,
                    "pending_count_reflection": len(pending),
                    "critic_success": critic_success,
                },
            )

        # LAT.4 — chunked persistence. Split each row into a compute phase
        # (LLM completion + embedding — slow, network-bound, no lock) and a
        # persist phase (fast DB insert). Chunks of up to CHUNK_SIZE prepared
        # rows are committed in ONE transaction each via pool.transaction(),
        # replacing one execute()-per-row autocommit. Compute intentionally
        # happens OUTSIDE the transaction: holding the single-writer lock
        # across a chunk's worth of sequential LLM calls would recreate, at
        # chunk granularity, the exact starvation this batching fixes.
        written = 0
        for chunk_start in range(0, len(pending), CHUNK_SIZE):
            chunk = pending[chunk_start:chunk_start + CHUNK_SIZE]
            prepared: list[_PreparedReflection] = []
            for outcome in chunk:
                p = await self._compute_reflection(outcome, provider, model)
                if p is not None:
                    prepared.append(p)
            if not prepared:
                continue
            # Crash-safety tradeoff (AC #5): a crash mid-chunk loses at most
            # this chunk's writes (<= CHUNK_SIZE rows), not the whole pending
            # set and not just one row — acceptable given WAL's
            # synchronous=NORMAL durability semantics (already in use,
            # unchanged by this story). A write failure inside the chunk
            # rolls back that whole chunk (pool.transaction()'s semantics) —
            # logged and skipped rather than aborting the rest of the job
            # (B5: one bad chunk must not lose already-prepared later rows).
            try:
                async with self._db.transaction() as tx:
                    for p in prepared:
                        await self._store.write(
                            trace_id=p.outcome.trace_id,
                            owl_name=p.outcome.owl_name,
                            summary=p.summary,
                            suggested_strategy=p.suggested_strategy,
                            failure_class=p.outcome.failure_class,
                            quality_score=p.outcome.quality_score,
                            embedding=p.embedding,
                            embedding_model=p.embedding_model,
                            conn=tx,
                        )
            except Exception as exc:  # B5
                log.memory.warning(
                    "[reflection] execute: chunk persist failed — rolled back, skipping chunk",
                    exc_info=exc,
                    extra={"_fields": {
                        "job_id": job.job_id, "chunk_size": len(prepared),
                        "chunk_start": chunk_start,
                    }},
                )
                continue
            written += len(prepared)
            log.memory.debug(
                "[reflection] execute: chunk committed",
                extra={"_fields": {
                    "job_id": job.job_id, "chunk_size": len(prepared),
                    "chunk_start": chunk_start,
                }},
            )
            # Best-effort LessonsIndex publish per row — outside the DB
            # transaction (LanceDB, not SQLite; never gates the DB write).
            for p in prepared:
                await self._publish_to_lessons(
                    outcome=p.outcome,
                    summary=p.summary, suggested_strategy=p.suggested_strategy,
                )

        duration_ms = (time.monotonic() - t0) * 1000
        # 5. EXIT — overall success requires the reflection pass to have run
        # cleanly; a critic-phase failure is surfaced via `error` but doesn't
        # flip success, since reflection still made real progress.
        log.memory.info(
            "[reflection] execute: exit",
            extra={"_fields": {
                "job_id": job.job_id, "scored": scored, "written": written,
                "pending_count_reflection": len(pending), "duration_ms": duration_ms,
            }},
        )
        return JobResult(
            job_id=job.job_id,
            effect_class="state_change", success=True,
            output=f"scored:{scored} written:{written}",
            error=critic_error, duration_ms=duration_ms,
            metadata={
                "scored": scored, "written": written,
                "pending_count_critic": pending_count_critic,
                "pending_count_reflection": len(pending),
                "critic_success": critic_success,
            },
        )

    async def _compute_reflection(
        self, outcome: TaskOutcome, provider: ModelProvider, model: str,
    ) -> _PreparedReflection | None:
        """Run one reflection's LLM completion + embedding (no DB write).

        LAT.4: split out of the old ``_reflect_one`` so the slow, network-
        bound part of a row's work never runs while a chunked persist
        transaction holds the single-writer lock (see ``execute``). Returns
        ``None`` on any failure — mirrors the prior per-row skip semantics.
        """
        # 1. ENTRY
        log.memory.debug(
            "[reflection] compute_reflection: entry",
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
            result = await provider.complete(messages, model=model)
        except Exception as exc:  # B5
            log.memory.warning(
                "[reflection] compute_reflection: provider.complete failed — skipping",
                exc_info=exc,
                extra={"_fields": {"outcome_id": outcome.outcome_id}},
            )
            return None
        # 2. DECISION (cont.) — parse
        parsed = parse_reflection_response(result.content)
        if parsed is None:
            log.memory.warning(
                "[reflection] compute_reflection: could not parse response — skipping",
                extra={"_fields": {
                    "outcome_id": outcome.outcome_id,
                    "raw_preview": result.content[:200],
                }},
            )
            return None
        summary, suggested_strategy = parsed

        # 3. STEP (cont.) — embed
        embedding, embedding_model = await self._embed(summary)
        # 4. EXIT
        log.memory.debug(
            "[reflection] compute_reflection: exit",
            extra={"_fields": {
                "outcome_id": outcome.outcome_id,
                "summary_len": len(summary),
            }},
        )
        return _PreparedReflection(
            outcome=outcome, summary=summary, suggested_strategy=suggested_strategy,
            embedding=embedding, embedding_model=embedding_model,
        )

    async def _publish_to_lessons(
        self, *, outcome: TaskOutcome, summary: str, suggested_strategy: str,
    ) -> None:
        """Best-effort: push the reflection into the cross-source LessonsIndex."""
        if self._lessons_index is None:
            return
        from stackowl.learning.lessons_index import LessonDraft

        # Compose the lesson content — same shape the LLM gets at retrieval.
        content = f"What worked for {outcome.owl_name}: {summary}"
        if suggested_strategy:
            content += f" Repeat: {suggested_strategy}"
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
