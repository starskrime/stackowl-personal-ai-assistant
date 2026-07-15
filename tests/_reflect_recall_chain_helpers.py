"""Shared test doubles for Story 1.1's reflect -> store -> recall chain tests.

Reused by ``tests/memory/test_reflect_recall_chain_e2e.py`` (Task 1 — direct
``_gather_lessons`` recall) and
``tests/pipeline/test_reflect_recall_gateway_integration.py`` (Task 3 — NFR-4
full ``classify.run()``-via-gateway recall) so both exercise the SAME
LessonsIndex/provider/outcome-seeding shapes instead of two divergent fakes.

Mirrors the patterns already established by
``tests/memory/test_reflection_writer_chunked_writes.py`` (scripted provider +
no-op critic) and ``tests/memory/test_reflection_capture.py`` (``_make_outcome``
shape) — nothing here is a new abstraction, just the shared plumbing those two
existing patterns need to be driven end-to-end instead of in isolation.
"""

from __future__ import annotations

import json
from pathlib import Path

from stackowl.db.pool import DbPool
from stackowl.embeddings.registry import EmbeddingRegistry
from stackowl.learning.lessons_index import LessonsIndex
from stackowl.learning.lessons_lance import LessonsLanceAdapter
from stackowl.memory.outcome_store import TaskOutcomeStore
from stackowl.providers.base import CompletionResult, Message
from stackowl.scheduler.job import Job, JobResult


class ScriptedReflectionProvider:
    """Deterministic fast-tier stub — always returns a valid reflection payload."""

    model_name = "stub-fast"

    def __init__(self, summary: str, suggested_strategy: str = "") -> None:
        self._summary = summary
        self._suggested_strategy = suggested_strategy

    @property
    def name(self) -> str:
        return "stub-fast"

    @property
    def protocol(self) -> str:  # type: ignore[override]
        return "openai"

    async def complete(
        self, messages: list[Message], model: str = "", **kwargs: object
    ) -> CompletionResult:
        return CompletionResult(
            content=json.dumps({
                "summary": self._summary,
                "suggested_strategy": self._suggested_strategy,
            }),
            model=self.model_name, provider_name="stub",
            input_tokens=0, output_tokens=0, duration_ms=1.0,
        )


class NoOpCritic:
    """Critic stub — outcomes are pre-scored, so the critic phase is skipped
    entirely (mirrors ``test_reflection_writer_chunked_writes.py``'s
    ``_NoOpCritic`` — avoids needing a second scripted provider call per row)."""

    defer_under_load = False

    async def execute(self, job: Job) -> JobResult:
        return JobResult(
            job_id=job.job_id, effect_class="state_change", success=True,
            output="noop", error=None, duration_ms=0.1,
            metadata={"scored": 0, "pending_count": 0},
        )


def reflection_job(job_id: str = "reflection_writer-e2e") -> Job:
    return Job(
        job_id=job_id, handler_name="reflection_writer",
        schedule="every 15m", idempotency_key=job_id,
        last_run_at=None, next_run_at="2026-07-15T00:00:00+00:00", status="running",
    )


def build_lessons_index(tmp_path: Path) -> LessonsIndex:
    """Real LessonsIndex over a temp-dir LanceDB + hash-fallback embeddings.

    ``EmbeddingRegistry()`` with no ``.create()`` call lazily defaults to
    ``HashEmbeddingProvider`` on first ``.get()`` — deterministic, zero
    network/model-download dependency (the Jetson dev box has no local model
    to pull; per project convention, never pull one just for a test).
    """
    adapter = LessonsLanceAdapter(data_dir=tmp_path / "lessons_lance")
    return LessonsIndex(adapter, embedding_registry=EmbeddingRegistry())


async def seed_outcome(
    db: DbPool, *, trace_id: str, owl_name: str, input_text: str,
    success: bool, quality_score: float | None, failure_class: str | None = None,
) -> None:
    """Insert one outcome + optionally set its quality_score.

    Mirrors ``tests/memory/test_reflection_capture.py``'s ``_make_outcome``
    helper shape (same store calls, same argument surface) — not imported
    directly since that helper is private to its own test module.
    """
    store = TaskOutcomeStore(db)
    await store.record(
        trace_id=trace_id, session_id="s", owl_name=owl_name, channel="cli",
        success=success, latency_ms=10.0, tool_call_count=0,
        failure_class=failure_class, step_durations={},
        input_text=input_text, response_text="did the thing",
    )
    if quality_score is not None:
        out = await store.get_by_trace_id(trace_id)
        assert out is not None
        await store.set_quality_score(out.outcome_id, quality_score)
