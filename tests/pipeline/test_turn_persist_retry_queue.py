import pytest

from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import ResponseChunk
from stackowl.pipeline.turn_persist import persist_turn


@pytest.mark.asyncio
async def test_floored_turn_creates_retry_queue_row(monkeypatch):
    inserted = {}

    class FakeRetryQueueStore:
        async def get_latest_pending_for_session(self, session_id):
            return None

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


@pytest.mark.asyncio
async def test_floored_turn_skips_insert_when_retry_already_pending(monkeypatch):
    """Live incident 2026-07-16: insert_pending() had no dedup, so a session
    with MANY floored turns (e.g. repeated 'AI news' asks during an unstable
    stretch) accumulated one independent retry_queue row per floor. Each later
    fired on its own via the 1-minute sweep — unprompted, disconnected from
    whatever the user was discussing by then, reading as the agent
    contradicting/forgetting itself. A pending row already in flight for the
    session must suppress a new one."""
    inserted = {}

    class _ExistingRow:
        id = "retry-existing-1"

    class FakeRetryQueueStore:
        async def get_latest_pending_for_session(self, session_id):
            return _ExistingRow()

        async def insert_pending(self, **kwargs):
            inserted.update(kwargs)
            return "retry-id-should-not-happen"

    class FakeServices:
        memory_bridge = None
        retry_queue_store = FakeRetryQueueStore()

    monkeypatch.setattr(
        "stackowl.pipeline.turn_persist.get_services", lambda: FakeServices()
    )

    state = PipelineState(
        trace_id="trace-y", session_id="sess-x", input_text="what's the latest AI news",
        channel="telegram", owl_name="secretary", pipeline_step="respond",
        responses=(
            ResponseChunk(
                content="I couldn't fully complete this...", is_final=False,
                chunk_index=0, trace_id="trace-y", owl_name="secretary", is_floor=True,
            ),
        ),
    )

    await persist_turn(state)

    assert inserted == {}


@pytest.mark.asyncio
async def test_retry_replay_floor_does_not_create_new_retry_queue_row(monkeypatch):
    """A floor on RetryActuator's OWN replay must not mint a second, independent
    retry_queue row — that compounding (a fresh attempt_count=0 row per floored
    replay) defeats the store's _MAX_ATTEMPTS circuit breaker and was observed
    to loop unboundedly. retry_replay=True is how retry_actuator.py marks its
    own replay state; persist_turn must skip insert_pending for it."""
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
        trace_id="retry-x", session_id="sess-x", input_text="prepare me for the interview",
        channel="telegram", owl_name="secretary", pipeline_step="respond",
        retry_replay=True,
        responses=(
            ResponseChunk(
                content="I couldn't fully complete this...", is_final=False,
                chunk_index=0, trace_id="retry-x", owl_name="secretary", is_floor=True,
            ),
        ),
    )

    await persist_turn(state)

    assert inserted == {}
