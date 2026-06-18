from __future__ import annotations

import pytest

from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps import deliver
from stackowl.pipeline.streaming import ResponseChunk, StreamRegistry


@pytest.mark.asyncio
async def test_registry_is_keyed_by_request_id_not_session() -> None:
    reg = StreamRegistry()
    w1, r1 = reg.create("req-1")
    w2, r2 = reg.create("req-2")
    assert reg.get_writer("req-1") is w1
    assert reg.get_writer("req-2") is w2
    assert reg.get_writer("session-x") is None  # session_id no longer a key
    reg.remove("req-1")
    assert reg.get_writer("req-1") is None
    assert reg.get_writer("req-2") is w2


def test_response_chunk_has_optional_target() -> None:
    chunk = ResponseChunk(
        content="hi", is_final=False, chunk_index=0,
        trace_id="req-1", owl_name="owl",
    )
    assert chunk.target is None
    tagged = chunk.model_copy(update={"target": 555})
    assert tagged.target == 555


# ---- deliver re-key + stream-miss hard-drop ---------------------------------


async def _drain(reader) -> list[ResponseChunk]:
    out: list[ResponseChunk] = []
    async for chunk in reader:
        out.append(chunk)
    return out


@pytest.mark.asyncio
async def test_deliver_resolves_writer_by_trace_id() -> None:
    """deliver looks up the writer by request_id (state.trace_id), not session_id."""
    reg = StreamRegistry()
    writer, reader = reg.create("req-deliver-1")  # keyed by request_id
    state = PipelineState(
        trace_id="req-deliver-1",
        session_id="sess-A",
        input_text="hi",
        channel="cli",
        owl_name="owl",
        pipeline_step="deliver",
        responses=(
            ResponseChunk(
                content="hello", is_final=False, chunk_index=0,
                trace_id="req-deliver-1", owl_name="owl",
            ),
        ),
    )
    token = set_services(StepServices(stream_registry=reg))
    try:
        await deliver.run(state)
    finally:
        reset_services(token)

    drained = await _drain(reader)
    assert [c.content for c in drained] == ["hello"]


@pytest.mark.asyncio
async def test_deliver_stream_miss_hard_drops_no_reroute() -> None:
    """A turn whose request_id has NO registered writer is dropped — never rerouted.

    A DIFFERENT request_id IS registered; deliver must NOT fall back to it.
    """
    reg = StreamRegistry()
    # A live writer for a DIFFERENT request — the wrong slot must stay empty.
    other_writer, other_reader = reg.create("req-other")
    state = PipelineState(
        trace_id="req-missing",
        session_id="sess-A",
        input_text="hi",
        channel="cli",
        owl_name="owl",
        pipeline_step="deliver",
        responses=(
            ResponseChunk(
                content="orphan", is_final=False, chunk_index=0,
                trace_id="req-missing", owl_name="owl",
            ),
        ),
    )
    token = set_services(StepServices(stream_registry=reg))
    try:
        result = await deliver.run(state)
    finally:
        reset_services(token)

    assert result is state  # returned, not raised
    # The unrelated writer received NOTHING — no reroute to a default slot.
    assert other_writer._queue.empty()


@pytest.mark.asyncio
async def test_deliver_drops_mismatched_chunk_request_id() -> None:
    """A chunk whose trace_id mismatches the turn's request_id is hard-dropped."""
    reg = StreamRegistry()
    writer, reader = reg.create("req-mix")
    state = PipelineState(
        trace_id="req-mix",
        session_id="sess-A",
        input_text="hi",
        channel="cli",
        owl_name="owl",
        pipeline_step="deliver",
        responses=(
            ResponseChunk(
                content="keep", is_final=False, chunk_index=0,
                trace_id="req-mix", owl_name="owl",
            ),
            ResponseChunk(
                content="alien", is_final=False, chunk_index=1,
                trace_id="req-OTHER", owl_name="owl",
            ),
        ),
    )
    token = set_services(StepServices(stream_registry=reg))
    try:
        await deliver.run(state)
    finally:
        reset_services(token)

    drained = await _drain(reader)
    assert [c.content for c in drained] == ["keep"]  # "alien" dropped
