"""Story 1.1 — prove the reflect -> store -> recall chain end-to-end (AC #1-4).

Unlike ``test_reflection_capture.py`` (write path only, ``ReflectionStore``
CRUD in isolation) and ``test_gather_lessons.py`` (recall path only, against a
``_FakeIndex`` that never saw a real write), this test drives ONE real
``LessonsIndex`` through BOTH hops: ``ReflectionWriterHandler._publish_to_lessons``
writes into it, then ``classify._gather_lessons`` reads from the SAME instance.
That seam — whether ``_publish_to_lessons``'s output shape is actually what
``_gather_lessons`` can consume — is what was previously untested.

POSITIVE-ONLY (see the story's Dev Notes): AC #1 uses the system's REAL
reflection-eligibility trigger (success=True, quality_score >= 0.6,
failure_class=None) — not a literal "failed task" outcome, which the platform
is deliberately built to never learn from (operator directive, mirrors
dna_attribution's own positive-only filter). AC #4 is the disqualifying-outcome
boundary case, asserting ABSENCE, not presence.

This story does not force a fix (AC #3) — it records whichever stage (write /
publish / recall) actually breaks, if any, in the Dev Agent Record for Story
1.2 to pick up.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stackowl.db.pool import DbPool
from stackowl.embeddings.registry import EmbeddingRegistry
from stackowl.memory.reflection_store import ReflectionStore
from stackowl.memory.reflection_writer_handler import ReflectionWriterHandler
from stackowl.pipeline import lesson_context as lc
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.steps.classify import _gather_lessons
from stackowl.providers.registry import ProviderRegistry
from tests._reflect_recall_chain_helpers import (
    NoOpCritic,
    ScriptedReflectionProvider,
    build_lessons_index,
    reflection_job,
    seed_outcome,
)

pytestmark = pytest.mark.asyncio

_SUMMARY = "web_fetch retries paid off for AWS billing questions"
_STRATEGY = "retry web_fetch once before giving up"


async def test_reflect_write_publish_recall_chain_e2e(
    tmp_db: DbPool, tmp_path: Path
) -> None:
    """AC #1 + AC #2 + AC #3, chained (not mocked/asserted in isolation).

    Stage 1 (write): an eligible outcome -> ReflectionWriterHandler.execute()
      -> a durable row in `reflections`.
    Stage 2 (publish): the SAME execute() call -> _publish_to_lessons() ->
      the REAL LessonsIndex (LanceDB, tmp-dir, hash-fallback embeddings).
    Stage 3 (recall): a LATER turn's classify._gather_lessons(), against the
      SAME LessonsIndex instance, surfaces the reflection's content.
    """
    lessons_index = build_lessons_index(tmp_path)
    registry = ProviderRegistry()
    registry.register_mock(
        "fast", ScriptedReflectionProvider(_SUMMARY, _STRATEGY), tier="fast",
    )
    handler = ReflectionWriterHandler(
        db=tmp_db, provider_registry=registry, embedding_registry=EmbeddingRegistry(),
        critic=NoOpCritic(), lessons_index=lessons_index,
    )

    # --- Stage 1: seed an outcome meeting the REAL positive-only trigger ------
    await seed_outcome(
        tmp_db, trace_id="trace-good", owl_name="scout",
        input_text="how do I check my AWS billing?",
        success=True, quality_score=0.85,
    )

    result = await handler.execute(reflection_job())
    assert result.success is True, f"reflection_writer.execute() failed: {result.error}"
    assert result.metadata["written"] == 1, (
        f"AC #1 FAILED — write stage: expected 1 row written, "
        f"metadata={result.metadata!r}"
    )

    # AC #1a: durably written to `reflections`.
    rstore = ReflectionStore(tmp_db)
    written = await rstore.get_by_trace_id("trace-good")
    assert written is not None, "AC #1 FAILED — write stage: no row in `reflections`"
    assert written.summary == _SUMMARY

    # AC #1b: published into the REAL LessonsIndex (isolates the publish hop
    # from the recall hop below — if this fails, the break is in
    # _publish_to_lessons, not in classify).
    published_hits = await lessons_index.search("AWS billing", limit=5)
    assert any(h.source_type == "reflection" for h in published_hits), (
        "AC #1 FAILED — publish stage: reflection was written but never reached "
        "the LessonsIndex (_publish_to_lessons did not publish it)"
    )

    # --- Stage 3: a LATER turn recalls it through the LIVE recall path --------
    services = StepServices(lessons_index=lessons_index)
    stoken = set_services(services)
    ltoken = lc.bind()
    try:
        block = await _gather_lessons("what do you know about AWS billing?", limit=3)
    finally:
        lc.reset(ltoken)
        reset_services(stoken)

    # AC #2 + AC #3: the recalled block carries the reflection's content.
    assert "## Cross-Source Lessons" in block, (
        f"AC #2 FAILED — recall stage: _gather_lessons produced no lesson block "
        f"even though the reflection was published. block={block!r}"
    )
    assert _SUMMARY in block, (
        f"AC #2 FAILED — recall stage: reflection content did not surface in the "
        f"recalled block. block={block!r}"
    )


async def test_disqualifying_outcome_writes_and_recalls_nothing(
    tmp_db: DbPool, tmp_path: Path
) -> None:
    """AC #4 — boundary case: an outcome that does NOT meet the positive-only
    trigger (a genuine failure, or a low-quality success) must produce no
    reflection and nothing recallable. Guards against the happy-path test
    passing by accident (e.g. an empty-string false positive)."""
    lessons_index = build_lessons_index(tmp_path)
    registry = ProviderRegistry()
    registry.register_mock(
        "fast", ScriptedReflectionProvider(_SUMMARY, _STRATEGY), tier="fast",
    )
    handler = ReflectionWriterHandler(
        db=tmp_db, provider_registry=registry, embedding_registry=EmbeddingRegistry(),
        critic=NoOpCritic(), lessons_index=lessons_index,
    )

    # A genuine failure — positive-only must exclude it (never "this failed").
    await seed_outcome(
        tmp_db, trace_id="trace-bad", owl_name="scout",
        input_text="how do I check my AWS billing?",
        success=False, quality_score=0.9, failure_class="OwlTimeoutError",
    )
    # A low-quality success — also excluded (quality_score < 0.6).
    await seed_outcome(
        tmp_db, trace_id="trace-low", owl_name="scout",
        input_text="how do I check my AWS billing?",
        success=True, quality_score=0.3,
    )

    result = await handler.execute(reflection_job("reflection_writer-e2e-neg"))
    assert result.success is True
    assert result.metadata["written"] == 0, (
        f"AC #4 FAILED — a disqualifying outcome was written: {result.metadata!r}"
    )

    rstore = ReflectionStore(tmp_db)
    assert await rstore.get_by_trace_id("trace-bad") is None
    assert await rstore.get_by_trace_id("trace-low") is None

    # Nothing was published — recall must legitimately find nothing, NOT
    # degrade (an empty result here must be "no lessons", never a masked error).
    services = StepServices(lessons_index=lessons_index)
    stoken = set_services(services)
    ltoken = lc.bind()
    try:
        block = await _gather_lessons("what do you know about AWS billing?", limit=3)
    finally:
        lc.reset(ltoken)
        reset_services(stoken)

    assert block == "", f"AC #4 FAILED — a disqualifying outcome surfaced a lesson: {block!r}"
