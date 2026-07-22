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
async def test_floored_turn_supersedes_existing_pending_row(monkeypatch):
    """Live incident 2026-07-16: insert_pending() had no dedup, so a session
    with MANY floored turns (e.g. repeated 'AI news' asks during an unstable
    stretch) accumulated one independent retry_queue row per floor. Each later
    fired on its own via the 1-minute sweep — unprompted, disconnected from
    whatever the user was discussing by then, reading as the agent
    contradicting/forgetting itself. A pending row already in flight for the
    session must suppress a SECOND independent row.

    Live incident 2026-07-21: the original fix suppressed the second row by
    just skipping it — silently dropping the user's newer ask with nothing
    ever retrying it. The fix must instead repoint the existing row at THIS
    turn's trace_id/goal (still one row per session, now tracking the
    freshest ask), not skip it outright."""
    inserted = {}
    superseded = {}

    class _ExistingRow:
        id = "retry-existing-1"

    class FakeRetryQueueStore:
        async def get_latest_pending_for_session(self, session_id):
            return _ExistingRow()

        async def insert_pending(self, **kwargs):
            inserted.update(kwargs)
            return "retry-id-should-not-happen"

        async def supersede(self, retry_id, **kwargs):
            superseded["retry_id"] = retry_id
            superseded.update(kwargs)

    class FakeServices:
        memory_bridge = None
        retry_queue_store = FakeRetryQueueStore()
        sticky_route_cache = None

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
    assert superseded["retry_id"] == "retry-existing-1"
    assert superseded["trace_id"] == "trace-y"
    assert superseded["goal"] == "what's the latest AI news"


@pytest.mark.asyncio
async def test_floored_turn_evicts_sticky_route_cache(monkeypatch):
    """Live incident 2026-07-21: a session's short follow-ups ('Yes review',
    'Yes') stayed sticky-routed to a stale 'conversational' classification
    (triage.py FR-9) across a floor AND that floor's own retry replay —
    neither ever got tool access, so both a vague future-promise floor and a
    'I don't have my tools' non-answer got delivered instead of a real
    answer. A floored turn must evict the session's sticky-route cache entry
    so the NEXT message re-runs the real router instead of inheriting the
    same tool-free routing."""
    evicted = []

    class FakeStickyRouteCache:
        def evict(self, session_id):
            evicted.append(session_id)

    class FakeServices:
        memory_bridge = None
        retry_queue_store = None
        sticky_route_cache = FakeStickyRouteCache()

    monkeypatch.setattr(
        "stackowl.pipeline.turn_persist.get_services", lambda: FakeServices()
    )

    state = PipelineState(
        trace_id="trace-z", session_id="sess-brain", input_text="Yes review",
        channel="telegram", owl_name="secretary", pipeline_step="respond",
        responses=(
            ResponseChunk(
                content="I said I'd do that later...", is_final=False,
                chunk_index=0, trace_id="trace-z", owl_name="secretary", is_floor=True,
            ),
        ),
    )

    await persist_turn(state)

    assert evicted == ["sess-brain"]


@pytest.mark.asyncio
async def test_clean_turn_does_not_evict_sticky_route_cache(monkeypatch):
    """A non-floored (successful) turn must not touch the sticky-route cache —
    eviction is specifically the "this routing just proved wrong" signal."""
    evicted = []

    class FakeStickyRouteCache:
        def evict(self, session_id):
            evicted.append(session_id)

    class FakeServices:
        memory_bridge = None
        retry_queue_store = None
        sticky_route_cache = FakeStickyRouteCache()

    monkeypatch.setattr(
        "stackowl.pipeline.turn_persist.get_services", lambda: FakeServices()
    )

    state = PipelineState(
        trace_id="trace-ok", session_id="sess-brain", input_text="thanks!",
        channel="telegram", owl_name="secretary", pipeline_step="respond",
        responses=(
            ResponseChunk(
                content="You're welcome!", is_final=True,
                chunk_index=0, trace_id="trace-ok", owl_name="secretary", is_floor=False,
            ),
        ),
    )

    await persist_turn(state)

    assert evicted == []


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
