"""STEER-2 (F100) — a computed answer is never silently dropped on stream-miss.

F100: ``deliver.run`` HARD-DROPPED a top-level turn's output when no StreamWriter
was registered for its ``trace_id`` — the responses were discarded with no retry,
queue, or proactive push. For a non-delegated top-level turn that IS a lost
answer (e.g. the user's terminal disconnected mid-turn, or the stream slot was
reaped). The fix: on a stream-miss for a top-level turn WITH a resolvable reply
target, fall back to a durable proactive send via ``reply_target`` so the answer
reaches the user. Delegated children (``delegation_depth>0``) keep returning via
the A2A response and must NOT proactive-send.
"""

from __future__ import annotations

import pytest

from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps import deliver as deliver_step
from stackowl.pipeline.streaming import ResponseChunk, StreamRegistry

pytestmark = pytest.mark.asyncio


class _RecordingDeliverer:
    """Captures proactive-deliver calls (stands in for ProactiveDeliverer)."""

    def __init__(self) -> None:
        self.delivered: list[object] = []

    async def deliver(self, notification: object) -> str:
        self.delivered.append(notification)
        return "delivered"


def _state(*, trace_id: str, depth: int = 0, target: int | str | None = 5) -> PipelineState:
    st = PipelineState(
        trace_id=trace_id,
        session_id="s1",
        input_text="hi",
        channel="telegram",
        owl_name="secretary",
        pipeline_step="deliver",
        delegation_depth=depth,
        reply_target=target,
    )
    return st.evolve(
        responses=[
            ResponseChunk(
                content="the answer ", is_final=False, chunk_index=0,
                trace_id=trace_id, owl_name="secretary",
            ),
            ResponseChunk(
                content="is 42", is_final=False, chunk_index=1,
                trace_id=trace_id, owl_name="secretary",
            ),
        ]
    )


async def test_stream_miss_top_level_falls_back_to_proactive(monkeypatch) -> None:
    """No writer for a top-level turn → the answer is proactively delivered, not dropped."""
    deliverer = _RecordingDeliverer()
    svc = StepServices(stream_registry=StreamRegistry(), proactive_deliverer=deliverer)  # type: ignore[arg-type]
    monkeypatch.setattr(deliver_step, "get_services", lambda: svc)

    # No writer registered for this trace_id → stream-miss.
    state = _state(trace_id="orphan-1", target=4242)
    out = await deliver_step.run(state)

    assert out is state  # state passthrough preserved
    # The computed answer was handed to the durable proactive path (not dropped).
    assert len(deliverer.delivered) == 1
    note = deliverer.delivered[0]
    assert note.message == "the answer is 42"
    assert note.channel_name == "telegram"
    assert note.target == 4242  # routed via the turn's own reply_target


async def test_stream_miss_delegated_child_does_not_proactive(monkeypatch) -> None:
    """A delegated child returns via A2A — a stream-miss must NOT proactive-send."""
    deliverer = _RecordingDeliverer()
    svc = StepServices(stream_registry=StreamRegistry(), proactive_deliverer=deliverer)  # type: ignore[arg-type]
    monkeypatch.setattr(deliver_step, "get_services", lambda: svc)

    state = _state(trace_id="child-1", depth=1)
    await deliver_step.run(state)
    assert deliverer.delivered == []  # never proactive-sends a child


async def test_stream_miss_no_target_does_not_proactive(monkeypatch) -> None:
    """A top-level turn with no resolvable reply target can't be proactively routed."""
    deliverer = _RecordingDeliverer()
    svc = StepServices(stream_registry=StreamRegistry(), proactive_deliverer=deliverer)  # type: ignore[arg-type]
    monkeypatch.setattr(deliver_step, "get_services", lambda: svc)

    # CLI-style turn (reply_target None): the adapter owns the terminal; a missing
    # writer there means the terminal is gone — no durable channel target to push to.
    state = _state(trace_id="cli-1", target=None)
    await deliver_step.run(state)
    assert deliverer.delivered == []


async def test_writer_present_does_not_proactive(monkeypatch) -> None:
    """When a writer IS registered the normal stream path runs — no proactive send."""
    deliverer = _RecordingDeliverer()
    registry = StreamRegistry()
    svc = StepServices(stream_registry=registry, proactive_deliverer=deliverer)  # type: ignore[arg-type]
    monkeypatch.setattr(deliver_step, "get_services", lambda: svc)

    state = _state(trace_id="live-1", target=7)
    registry.create(state.trace_id)
    await deliver_step.run(state)
    assert deliverer.delivered == []  # delivered via stream, not proactive
