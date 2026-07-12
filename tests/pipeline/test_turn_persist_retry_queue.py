import pytest

from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import ResponseChunk
from stackowl.pipeline.turn_persist import persist_turn


@pytest.mark.asyncio
async def test_floored_turn_creates_retry_queue_row(monkeypatch):
    inserted = {}

    class FakeRetryQueueStore:
        async def insert_pending(self, **kwargs):
            inserted.update(kwargs)
            return "retry-id-1"

    class FakeServices:
        memory_bridge = None
        retry_queue_store = FakeRetryQueueStore()

    monkeypatch.setattr(
        "stackowl.pipeline.turn_persist.get_services", lambda: FakeServices()
    )

    state = PipelineState(
        trace_id="trace-x", session_id="sess-x", input_text="prepare me for the interview",
        channel="telegram", owl_name="secretary", pipeline_step="respond",
        responses=(
            ResponseChunk(
                content="I couldn't fully complete this...", is_final=False,
                chunk_index=0, trace_id="trace-x", owl_name="secretary", is_floor=True,
            ),
        ),
    )

    await persist_turn(state)

    assert inserted["trace_id"] == "trace-x"
    assert inserted["session_id"] == "sess-x"
    assert inserted["goal"] == "prepare me for the interview"
