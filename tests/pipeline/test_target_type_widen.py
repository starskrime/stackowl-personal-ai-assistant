"""A1 — per-message delivery target widened to ``int | str | None``.

Slack targets are STRINGS (channel id ``C123…``, ``thread_ts`` ``1234.5678``);
Telegram targets are ints (chat_id). The shared carrier fields
(IngressMessage.chat_id / PipelineState.reply_target / ResponseChunk.target /
Turn.target) must accept BOTH so a Slack string can flow through the same
per-message routing path. Telegram's int path stays untouched.
"""

from __future__ import annotations

import pytest

from stackowl.gateway.scanner import IngressMessage
from stackowl.gateway.turn_registry import Turn, TurnStatus
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps import deliver
from stackowl.pipeline.streaming import ResponseChunk, StreamRegistry


async def _drain(reader) -> list[ResponseChunk]:
    out: list[ResponseChunk] = []
    async for chunk in reader:
        out.append(chunk)
    return out


# (a) string target accepted on ResponseChunk -------------------------------


def test_response_chunk_accepts_string_target() -> None:
    chunk = ResponseChunk(
        content="hi", is_final=False, chunk_index=0,
        trace_id="req-1", owl_name="owl",
        target="C123:1234.5678",
    )
    assert chunk.target == "C123:1234.5678"


# (b) string reply_target survives end-to-end through deliver ---------------


@pytest.mark.asyncio
async def test_deliver_stamps_string_target_end_to_end() -> None:
    """A PipelineState string reply_target is stamped onto the delivered chunk."""
    reg = StreamRegistry()
    writer, reader = reg.create("req-slack-1")
    state = PipelineState(
        trace_id="req-slack-1",
        session_id="sess-A",
        input_text="hi",
        channel="slack",
        owl_name="owl",
        pipeline_step="deliver",
        reply_target="C123",
        responses=(
            ResponseChunk(
                content="hello", is_final=False, chunk_index=0,
                trace_id="req-slack-1", owl_name="owl",
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
    assert drained[0].target == "C123"  # string survived end-to-end


# (c) string chat_id accepted on IngressMessage -----------------------------


def test_ingress_message_accepts_string_chat_id() -> None:
    msg = IngressMessage(
        text="hi", session_id="s", channel="slack", trace_id="t",
        chat_id="thread.ts",
    )
    assert msg.chat_id == "thread.ts"


# (c') string target accepted on Turn ---------------------------------------


def test_turn_accepts_string_target() -> None:
    turn = Turn(
        turn_id="r1", session_id="s", task=None,
        target="C123:1234.5678", original_input="hi",
    )
    assert turn.target == "C123:1234.5678"
    assert turn.status is TurnStatus.RUNNING


# (d) the EXISTING int path still works -------------------------------------


def test_int_path_still_works() -> None:
    chunk = ResponseChunk(
        content="hi", is_final=False, chunk_index=0,
        trace_id="req-1", owl_name="owl", target=123,
    )
    assert chunk.target == 123

    state = PipelineState(
        trace_id="t", session_id="s", input_text="hi",
        channel="telegram", owl_name="owl", pipeline_step="deliver",
        reply_target=456,
    )
    assert state.reply_target == 456

    msg = IngressMessage(
        text="hi", session_id="s", channel="telegram", trace_id="t", chat_id=789,
    )
    assert msg.chat_id == 789
