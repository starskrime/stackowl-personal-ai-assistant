"""Tests for Learning Commit 2 — Reflection writer + store + filter."""

from __future__ import annotations

import pytest

from stackowl.db.pool import DbPool
from stackowl.memory.json_parser import parse_json_response
from stackowl.memory.outcome_store import TaskOutcome, TaskOutcomeStore
from stackowl.memory.reflection_prompt import (
    ReflectionPromptBuilder,
    parse_reflection_response,
)
from stackowl.memory.reflection_store import Reflection, ReflectionStore

pytestmark = pytest.mark.asyncio


# --- json_parser shared helper ---------------------------------------------

def test_parse_json_response_extracts_clean_object() -> None:
    assert parse_json_response('{"a": 1, "b": "x"}') == {"a": 1, "b": "x"}


def test_parse_json_response_strips_json_fence() -> None:
    raw = '```json\n{"k": "v"}\n```'
    assert parse_json_response(raw) == {"k": "v"}


def test_parse_json_response_strips_leading_prose() -> None:
    raw = 'Here is your answer: {"score": 0.5}'
    assert parse_json_response(raw) == {"score": 0.5}


def test_parse_json_response_returns_none_for_garbage() -> None:
    assert parse_json_response("not json") is None
    assert parse_json_response("[1, 2, 3]") is None  # array, not object


def test_parse_json_response_enforces_required_keys() -> None:
    raw = '{"a": 1}'
    assert parse_json_response(raw, required_keys=["a", "b"]) is None
    assert parse_json_response(raw, required_keys=["a"]) == {"a": 1}


# --- parse_reflection_response ---------------------------------------------

def test_parse_reflection_response_returns_summary_and_strategy() -> None:
    raw = '{"summary": "tool X timed out", "suggested_strategy": "use Y instead"}'
    assert parse_reflection_response(raw) == ("tool X timed out", "use Y instead")


def test_parse_reflection_response_strips_whitespace() -> None:
    raw = '{"summary": "  x  ", "suggested_strategy": "  y  "}'
    assert parse_reflection_response(raw) == ("x", "y")


def test_parse_reflection_response_rejects_empty_summary() -> None:
    raw = '{"summary": "", "suggested_strategy": "y"}'
    assert parse_reflection_response(raw) is None


def test_parse_reflection_response_tolerates_missing_strategy() -> None:
    # Spec says both keys required, so this returns None (validator catches it).
    raw = '{"summary": "x"}'
    assert parse_reflection_response(raw) is None


def test_parse_reflection_response_handles_fenced_block() -> None:
    raw = '```json\n{"summary": "a", "suggested_strategy": "b"}\n```'
    assert parse_reflection_response(raw) == ("a", "b")


# --- ReflectionPromptBuilder -----------------------------------------------

def test_reflection_prompt_is_positive_and_includes_trace() -> None:
    # POSITIVE-ONLY: the prompt coaches on what WORKED, never on failure.
    outcome = TaskOutcome(
        outcome_id=1, trace_id="t1", session_id="s", owl_name="scout",
        channel="cli", success=True, latency_ms=800.0, tool_call_count=3,
        failure_class=None, quality_score=0.9,
        step_durations={"execute": 700.0}, input_text="research stuff",
        response_text="(great answer)", captured_at=1.0, scored_at=2.0,
    )
    msgs = ReflectionPromptBuilder().build(outcome)
    assert len(msgs) == 2
    user_text = msgs[1].content
    assert "research stuff" in user_text
    assert "scout" in user_text
    # System message is positively framed and states the JSON schema.
    system_low = msgs[0].content.lower()
    assert "worked" in system_low or "winning" in system_low
    assert "failure" not in system_low or "never frame anything as a failure" in system_low
    assert "summary" in msgs[0].content
    assert "suggested_strategy" in msgs[0].content


# --- ReflectionStore CRUD ---------------------------------------------------

async def _make_outcome(
    db: DbPool, *, trace_id: str, success: bool = False,
    quality_score: float | None = None, failure_class: str | None = None,
    score_it: bool = True,
) -> int:
    """Helper: insert one outcome via TaskOutcomeStore + optionally set quality_score."""
    store = TaskOutcomeStore(db)
    await store.record(
        trace_id=trace_id, session_id="s", owl_name="secretary", channel="cli",
        success=success, latency_ms=10.0, tool_call_count=0,
        failure_class=failure_class, step_durations={},
        input_text="hi", response_text="hello",
    )
    out = await store.get_by_trace_id(trace_id)
    assert out is not None
    if score_it and quality_score is not None:
        await store.set_quality_score(out.outcome_id, quality_score)
    return out.outcome_id


async def test_list_pending_returns_high_quality_successes(tmp_db: DbPool) -> None:
    """POSITIVE-ONLY: a successful, high-quality outcome IS eligible (learn the win)."""
    rstore = ReflectionStore(tmp_db)
    await _make_outcome(tmp_db, trace_id="good", quality_score=0.85, success=True)
    pending = await rstore.list_pending()
    assert len(pending) == 1
    assert pending[0].trace_id == "good"


async def test_list_pending_excludes_low_quality_outcomes(tmp_db: DbPool) -> None:
    """POSITIVE-ONLY: quality_score < 0.6 is NOT learned (no 'this was mediocre' memory)."""
    rstore = ReflectionStore(tmp_db)
    await _make_outcome(tmp_db, trace_id="lo", quality_score=0.3, success=True)
    pending = await rstore.list_pending()
    assert pending == []


async def test_list_pending_excludes_failures(tmp_db: DbPool) -> None:
    """POSITIVE-ONLY: a failure is NEVER learned (no 'this didn't work / I can't' memory)."""
    rstore = ReflectionStore(tmp_db)
    await _make_outcome(
        tmp_db, trace_id="err", quality_score=0.9, success=False,
        failure_class="OwlTimeoutError",
    )
    pending = await rstore.list_pending()
    assert pending == []


async def test_list_pending_excludes_unscored_outcomes(tmp_db: DbPool) -> None:
    """Outcomes without a quality_score yet (critic hasn't run) are NOT eligible."""
    rstore = ReflectionStore(tmp_db)
    await _make_outcome(tmp_db, trace_id="unscored", success=True, score_it=False)
    pending = await rstore.list_pending()
    assert pending == []


async def test_list_pending_excludes_already_reflected(tmp_db: DbPool) -> None:
    """Once a reflection exists for a trace_id, it's excluded from list_pending."""
    rstore = ReflectionStore(tmp_db)
    # A high-quality success (eligible under positive-only) that has already been
    # reflected — so the exclusion is by the reflection, not by the quality filter.
    await _make_outcome(tmp_db, trace_id="r1", quality_score=0.85, success=True)
    # Reflect on it.
    await rstore.write(
        trace_id="r1", owl_name="secretary",
        summary="test", suggested_strategy="test strat",
        failure_class=None, quality_score=0.85,
        embedding=None, embedding_model=None,
    )
    pending = await rstore.list_pending()
    assert pending == []


async def test_write_is_idempotent_on_trace_id(tmp_db: DbPool) -> None:
    rstore = ReflectionStore(tmp_db)
    await _make_outcome(tmp_db, trace_id="dup", quality_score=0.4, success=True)
    for _ in range(3):
        await rstore.write(
            trace_id="dup", owl_name="x", summary="s",
            suggested_strategy="strat", failure_class=None,
            quality_score=0.4, embedding=None, embedding_model=None,
        )
    rows = await tmp_db.fetch_all(
        "SELECT COUNT(*) AS cnt FROM reflections WHERE trace_id = ?", ("dup",),
    )
    assert rows[0]["cnt"] == 1


async def test_get_by_trace_id_round_trips_embedding(tmp_db: DbPool) -> None:
    rstore = ReflectionStore(tmp_db)
    await _make_outcome(tmp_db, trace_id="emb", quality_score=0.5, success=True)
    embedding = [0.1, 0.2, 0.3, 0.4]
    await rstore.write(
        trace_id="emb", owl_name="scout", summary="took too long",
        suggested_strategy="cache the result", failure_class=None,
        quality_score=0.5, embedding=embedding, embedding_model="stub-v1",
    )
    ref = await rstore.get_by_trace_id("emb")
    assert ref is not None
    assert isinstance(ref, Reflection)
    assert ref.summary == "took too long"
    assert ref.suggested_strategy == "cache the result"
    assert ref.embedding_model == "stub-v1"
    # pack/unpack round-trip preserves values within float32 precision.
    assert ref.embedding is not None
    assert len(ref.embedding) == 4
    assert ref.embedding[0] == pytest.approx(0.1, abs=1e-6)


async def test_recent_for_owl_returns_newest_first(tmp_db: DbPool) -> None:
    rstore = ReflectionStore(tmp_db)
    # Three outcomes + reflections in order.
    for i in range(3):
        await _make_outcome(
            tmp_db, trace_id=f"o{i}", quality_score=0.3, success=True,
        )
        await rstore.write(
            trace_id=f"o{i}", owl_name="scout",
            summary=f"reflection {i}", suggested_strategy="...",
            failure_class=None, quality_score=0.3,
            embedding=None, embedding_model=None,
        )
    recent = await rstore.recent_for_owl("scout", limit=10)
    assert [r.summary for r in recent] == ["reflection 2", "reflection 1", "reflection 0"]


async def test_recent_for_owl_filters_by_owl_name(tmp_db: DbPool) -> None:
    rstore = ReflectionStore(tmp_db)
    await _make_outcome(tmp_db, trace_id="a", quality_score=0.3, success=True)
    await _make_outcome(tmp_db, trace_id="b", quality_score=0.3, success=True)
    await rstore.write(
        trace_id="a", owl_name="scout", summary="scout note",
        suggested_strategy="", failure_class=None, quality_score=0.3,
        embedding=None, embedding_model=None,
    )
    await rstore.write(
        trace_id="b", owl_name="librarian", summary="librarian note",
        suggested_strategy="", failure_class=None, quality_score=0.3,
        embedding=None, embedding_model=None,
    )
    scout_reflections = await rstore.recent_for_owl("scout")
    librarian_reflections = await rstore.recent_for_owl("librarian")
    assert [r.summary for r in scout_reflections] == ["scout note"]
    assert [r.summary for r in librarian_reflections] == ["librarian note"]
