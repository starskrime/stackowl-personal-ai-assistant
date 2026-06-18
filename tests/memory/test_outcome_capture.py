"""Tests for Learning Commit 1 — TaskOutcomeStore + critic + backend capture."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Literal

import pytest

from stackowl.db.pool import DbPool
from stackowl.memory.critic_prompt import CriticScorerPromptBuilder, parse_critic_response
from stackowl.memory.outcome_store import TaskOutcome, TaskOutcomeStore, classify_failure
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend, _capture_outcome
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState, ToolCall
from stackowl.pipeline.streaming import ResponseChunk
from stackowl.providers.base import CompletionResult, Message, ModelProvider

pytestmark = pytest.mark.asyncio


# --- classify_failure --------------------------------------------------------

def test_classify_failure_returns_none_for_empty_errors() -> None:
    assert classify_failure(()) is None


def test_classify_failure_strips_step_prefix_and_returns_exception_class() -> None:
    assert classify_failure(("triage: ProviderError: upstream down",)) == "ProviderError"
    assert classify_failure(("execute: OwlTimeoutError: budget exceeded",)) == "OwlTimeoutError"


def test_classify_failure_uses_first_error_when_multiple() -> None:
    errs = ("triage: ProviderError: x", "execute: ToolExecutionError: y")
    assert classify_failure(errs) == "ProviderError"


def test_classify_failure_falls_back_to_truncated_string_when_unparseable() -> None:
    # No colon at all, no exception-class shape.
    result = classify_failure(("something went sideways",))
    assert result is not None
    assert "something went sideways" in result


# --- TaskOutcomeStore CRUD ---------------------------------------------------

async def test_record_inserts_one_row_and_starts_unscored(tmp_db: DbPool) -> None:
    store = TaskOutcomeStore(db=tmp_db)
    await store.record(
        trace_id="t1", session_id="s1", owl_name="secretary", channel="cli",
        success=True, latency_ms=42.0, tool_call_count=2, failure_class=None,
        step_durations={"triage": 10.0, "execute": 30.0},
        input_text="hi", response_text="hello",
    )
    out = await store.get_by_trace_id("t1")
    assert out is not None
    assert out.success is True
    assert out.tool_call_count == 2
    assert out.failure_class is None
    assert out.quality_score is None
    assert out.scored_at is None
    assert out.step_durations == {"triage": 10.0, "execute": 30.0}


async def test_record_is_idempotent_on_trace_id(tmp_db: DbPool) -> None:
    store = TaskOutcomeStore(db=tmp_db)
    for _ in range(3):
        await store.record(
            trace_id="dup", session_id="s", owl_name="x", channel="cli",
            success=True, latency_ms=1.0, tool_call_count=0, failure_class=None,
            step_durations={}, input_text="a", response_text="b",
        )
    rows = await tmp_db.fetch_all(
        "SELECT COUNT(*) AS cnt FROM task_outcomes WHERE trace_id = ?", ("dup",),
    )
    assert rows[0]["cnt"] == 1


async def test_list_pending_critic_returns_only_unscored(tmp_db: DbPool) -> None:
    store = TaskOutcomeStore(db=tmp_db)
    for tid in ("a", "b", "c"):
        await store.record(
            trace_id=tid, session_id="s", owl_name="x", channel="cli",
            success=True, latency_ms=1.0, tool_call_count=0, failure_class=None,
            step_durations={}, input_text="", response_text="",
        )
    # Score one of them.
    out_b = await store.get_by_trace_id("b")
    assert out_b is not None
    await store.set_quality_score(out_b.outcome_id, 0.9)

    pending = await store.list_pending_critic()
    trace_ids = {o.trace_id for o in pending}
    assert trace_ids == {"a", "c"}


async def test_set_quality_score_sets_scored_at(tmp_db: DbPool) -> None:
    store = TaskOutcomeStore(db=tmp_db)
    await store.record(
        trace_id="t", session_id="s", owl_name="x", channel="cli",
        success=True, latency_ms=1.0, tool_call_count=0, failure_class=None,
        step_durations={}, input_text="", response_text="",
    )
    out = await store.get_by_trace_id("t")
    assert out is not None
    await store.set_quality_score(out.outcome_id, 0.55)
    out2 = await store.get_by_trace_id("t")
    assert out2 is not None
    assert out2.quality_score == pytest.approx(0.55)
    assert out2.scored_at is not None


# --- TaskOutcomeStore.recent_for_session (live action recall) ----------------

async def test_recent_for_session_returns_newest_first(tmp_db: DbPool) -> None:
    store = TaskOutcomeStore(db=tmp_db)
    # Insert oldest -> newest; captured_at is time.time() so insert order = age.
    for tid in ("old", "mid", "new"):
        await store.record(
            trace_id=tid, session_id="sess", owl_name="x", channel="cli",
            success=True, latency_ms=1.0, tool_call_count=0, failure_class=None,
            step_durations={}, input_text=f"in-{tid}", response_text=f"out-{tid}",
        )
    recent = await store.recent_for_session("sess", limit=3)
    assert [o.trace_id for o in recent] == ["new", "mid", "old"]


async def test_recent_for_session_excludes_in_flight_trace(tmp_db: DbPool) -> None:
    store = TaskOutcomeStore(db=tmp_db)
    for tid in ("prior", "inflight"):
        await store.record(
            trace_id=tid, session_id="sess", owl_name="x", channel="cli",
            success=True, latency_ms=1.0, tool_call_count=0, failure_class=None,
            step_durations={}, input_text=f"in-{tid}", response_text="r",
        )
    recent = await store.recent_for_session(
        "sess", limit=3, exclude_trace_id="inflight",
    )
    assert [o.trace_id for o in recent] == ["prior"]


async def test_recent_for_session_empty_for_unknown_session(tmp_db: DbPool) -> None:
    store = TaskOutcomeStore(db=tmp_db)
    await store.record(
        trace_id="t", session_id="known", owl_name="x", channel="cli",
        success=True, latency_ms=1.0, tool_call_count=0, failure_class=None,
        step_durations={}, input_text="i", response_text="r",
    )
    assert await store.recent_for_session("nope") == []


async def test_recent_for_session_empty_for_nonpositive_limit(tmp_db: DbPool) -> None:
    store = TaskOutcomeStore(db=tmp_db)
    await store.record(
        trace_id="t", session_id="sess", owl_name="x", channel="cli",
        success=True, latency_ms=1.0, tool_call_count=0, failure_class=None,
        step_durations={}, input_text="i", response_text="r",
    )
    assert await store.recent_for_session("sess", limit=0) == []
    assert await store.recent_for_session("sess", limit=-1) == []


# --- CriticPromptBuilder + parse_critic_response -----------------------------

def test_critic_prompt_builds_two_messages_with_trace_summary() -> None:
    outcome = TaskOutcome(
        outcome_id=1, trace_id="t", session_id="s", owl_name="secretary",
        channel="cli", success=True, latency_ms=120.0, tool_call_count=1,
        failure_class=None, quality_score=None,
        step_durations={"triage": 10.0}, input_text="what is 2+2?",
        response_text="4", captured_at=1.0, scored_at=None,
    )
    msgs = CriticScorerPromptBuilder().build(outcome)
    assert len(msgs) == 2
    assert msgs[0].role == "system"
    assert msgs[1].role == "user"
    # User message includes full trace contents.
    user_text = msgs[1].content
    assert "what is 2+2?" in user_text
    assert "4" in user_text
    assert "secretary" in user_text
    assert "triage" in user_text


def test_parse_critic_response_handles_clean_json() -> None:
    assert parse_critic_response('{"score": 0.8, "reason": "good"}') == 0.8


def test_parse_critic_response_handles_fenced_block() -> None:
    raw = '```json\n{"score": 0.42}\n```'
    assert parse_critic_response(raw) == pytest.approx(0.42)


def test_parse_critic_response_clamps_out_of_range() -> None:
    assert parse_critic_response('{"score": 1.7}') == 1.0
    assert parse_critic_response('{"score": -0.5}') == 0.0


def test_parse_critic_response_returns_none_on_garbage() -> None:
    assert parse_critic_response("not json at all") is None
    assert parse_critic_response('{"wrong_key": 0.5}') is None
    assert parse_critic_response('{"score": "not a number"}') is None


# --- AsyncioBackend.run() captures outcome end-to-end -----------------------

class _StubProvider(ModelProvider):
    @property
    def name(self) -> str:
        return "stub"

    @property
    def protocol(self) -> Literal["openai", "anthropic", "gemini"]:
        return "openai"

    async def complete(self, messages: list[Message], model: str, **kwargs: object) -> CompletionResult:  # noqa: ARG002
        return CompletionResult(
            content="", input_tokens=0, output_tokens=0,
            model="stub", provider_name="stub", duration_ms=0.0,
        )

    async def stream(self, messages: list[Message], model: str, **kwargs: object) -> AsyncIterator[str]:  # noqa: ARG002
        if False:  # pragma: no cover
            yield ""
        return


async def test_capture_outcome_writes_row_with_step_durations(tmp_db: DbPool) -> None:
    services = StepServices(db_pool=tmp_db)
    state = PipelineState(
        trace_id="t-cap", session_id="s-cap", input_text="hi",
        channel="cli", owl_name="secretary", pipeline_step="deliver",
        responses=(
            ResponseChunk(content="hello", is_final=True, chunk_index=0,
                          trace_id="t-cap", owl_name="secretary"),
        ),
        tool_calls=(
            ToolCall(tool_name="read_file", args={"path": "/x"}, result="ok",
                     error=None, duration_ms=12.0),
        ),
        step_durations=(("triage", 5.0), ("execute", 200.0)),
        errors=(),
    )
    await _capture_outcome(state, total_ms=247.0, services=services)

    store = TaskOutcomeStore(tmp_db)
    out = await store.get_by_trace_id("t-cap")
    assert out is not None
    assert out.success is True
    assert out.tool_call_count == 1
    assert out.latency_ms == pytest.approx(247.0)
    assert out.failure_class is None
    assert out.step_durations == {"triage": 5.0, "execute": 200.0}
    assert out.response_text == "hello"


async def test_capture_outcome_records_failure_when_errors_present(tmp_db: DbPool) -> None:
    services = StepServices(db_pool=tmp_db)
    state = PipelineState(
        trace_id="t-err", session_id="s-err", input_text="hi",
        channel="cli", owl_name="secretary", pipeline_step="deliver",
        errors=("execute: OwlTimeoutError: deadline exceeded",),
    )
    await _capture_outcome(state, total_ms=15000.0, services=services)

    out = await TaskOutcomeStore(tmp_db).get_by_trace_id("t-err")
    assert out is not None
    assert out.success is False
    assert out.failure_class == "OwlTimeoutError"


async def test_capture_outcome_is_noop_when_no_db_pool() -> None:
    services = StepServices(db_pool=None)
    state = PipelineState(
        trace_id="t-nodb", session_id="s", input_text="hi",
        channel="cli", owl_name="x", pipeline_step="deliver",
    )
    # Must not raise.
    await _capture_outcome(state, total_ms=10.0, services=services)


async def test_backend_run_populates_step_durations_on_state(tmp_db: DbPool) -> None:
    """The backend's step loop now appends to PipelineState.step_durations."""
    from stackowl.pipeline import registry as reg_module
    from stackowl.pipeline.steps import deliver as deliver_module

    async def _capture_step(state: PipelineState) -> PipelineState:
        return state

    async def _noop_deliver(s: PipelineState) -> PipelineState:
        return s

    orig_steps = list(reg_module.PIPELINE_STEPS)
    orig_deliver = deliver_module.run
    reg_module.PIPELINE_STEPS[:] = [("triage", _capture_step), ("execute", _capture_step)]
    deliver_module.run = _noop_deliver  # type: ignore[assignment]
    try:
        backend = AsyncioBackend(services=StepServices(db_pool=tmp_db))
        state = PipelineState(
            trace_id="t-dur", session_id="s", input_text="hi",
            channel="cli", owl_name="secretary", pipeline_step="start",
        )
        final = await backend.run(state)
    finally:
        reg_module.PIPELINE_STEPS[:] = orig_steps
        deliver_module.run = orig_deliver  # type: ignore[assignment]

    step_names = {name for name, _ in final.step_durations}
    assert step_names == {"triage", "execute", "deliver"}
    # And the backend persisted an outcome row.
    out = await TaskOutcomeStore(tmp_db).get_by_trace_id("t-dur")
    assert out is not None
    assert out.step_durations.keys() == {"triage", "execute", "deliver"}
