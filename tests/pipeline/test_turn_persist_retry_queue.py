import pytest

from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import ResponseChunk
from stackowl.pipeline.turn_persist import persist_turn


@pytest.mark.asyncio
async def test_floored_turn_creates_a_retry_TASK(monkeypatch):
    """A floored turn retries ON THE ONE LOOP, not in a queue of its own.

    REWRITTEN 2026-08-28, not deleted. This used to assert an insert into
    `retry_queue` — a SECOND engine that held nothing `tasks` does not already
    hold, and whose own docstring said it re-armed "forever — no attempt cap, no
    terminal give-up". That unbounded policy is what messaged Bakir for hours from
    5,766 rows, and collapsing it into `tasks` is his standing rule of 2026-08-17:
    everything is a TASK on ONE loop, and no implementation may duplicate logic
    that already runs work.

    The old assertions live on unchanged in meaning — trace, session and goal must
    still reach the retry — because the ENGINE changed and the contract did not.
    """
    enqueued = []

    class _FakeTaskStore:
        async def enqueue(self, task):
            enqueued.append(task)

    services = StepServices(durable_task_store=_FakeTaskStore())
    monkeypatch.setattr(
        "stackowl.pipeline.turn_persist.get_services", lambda: services
    )

    state = PipelineState(
        trace_id="trace-x", session_key="sess-x", input_text="prepare me for the interview",
        channel="telegram", owl_name="secretary", pipeline_step="respond",
        responses=(
            ResponseChunk(
                content="I couldn't fully complete this...", is_final=False,
                chunk_index=0, trace_id="trace-x", owl_name="secretary", is_floor=True,
            ),
        ),
    )

    await persist_turn(state)

    retries = [t for t in enqueued if t.trigger_kind == "retry"]
    assert len(retries) == 1
    assert retries[0].session_key == "sess-x"
    assert retries[0].goal == "prepare me for the interview"
    assert retries[0].max_attempts > 0, (
        "the retry must be BOUNDED — the queue this replaced re-armed for ever"
    )


@pytest.mark.asyncio
async def test_a_second_floor_repoints_the_in_flight_retry(monkeypatch):
    """Live incident 2026-07-16: insert_pending() had no dedup, so a session
    with MANY floored turns (e.g. repeated 'AI news' asks during an unstable
    stretch) accumulated one independent retry row per floor. Each later
    fired on its own via the 1-minute sweep — unprompted, disconnected from
    whatever the user was discussing by then, reading as the agent
    contradicting/forgetting itself. A retry already in flight for the
    session must suppress a SECOND independent row.

    Live incident 2026-07-21: the original fix suppressed the second row by
    just skipping it — silently dropping the user's newer ask with nothing
    ever retrying it. The fix must instead repoint the existing row at THIS
    turn's goal (still one row per session, now tracking the freshest ask),
    not skip it outright.

    BOTH INCIDENTS SURVIVE THE MOVE TO THE ONE LOOP, which is the whole reason
    this test was rewritten rather than deleted. What changed is the MECHANISM:
    dedup is now `idempotency_key`, which migration 0124 finally ENFORCES with a
    partial unique index (it was previously stored and unindexed — a column
    nothing read). The collision is the expected path, and it repoints.
    """
    enqueued = []
    repointed = {}

    class _FakeTaskStore:
        async def enqueue(self, task):
            # What the enforced unique index does on a live duplicate key.
            raise RuntimeError(
                "UNIQUE constraint failed: tasks.owner_id, tasks.idempotency_key"
            )

        async def repoint_retry(self, *, idempotency_key, goal, trace_id):
            repointed.update(
                idempotency_key=idempotency_key, goal=goal, trace_id=trace_id
            )
            return True

    services = StepServices(
        durable_task_store=_FakeTaskStore(), sticky_route_cache=None,
    )
    monkeypatch.setattr(
        "stackowl.pipeline.turn_persist.get_services", lambda: services
    )

    state = PipelineState(
        trace_id="trace-y", session_key="sess-x", input_text="what's the latest AI news",
        channel="telegram", owl_name="secretary", pipeline_step="respond",
        responses=(
            ResponseChunk(
                content="I couldn't fully complete this...", is_final=False,
                chunk_index=0, trace_id="trace-y", owl_name="secretary", is_floor=True,
            ),
        ),
    )

    await persist_turn(state)

    assert not enqueued, "a second independent retry row was created (incident 2026-07-16)"
    assert repointed.get("goal") == "what's the latest AI news", (
        "the newer ask was DROPPED rather than repointed (incident 2026-07-21)"
    )
    assert repointed.get("idempotency_key") == "retry:sess-x"



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
        def evict(self, session_key):
            evicted.append(session_key)

    services = StepServices(
        retry_queue_store=None,
        sticky_route_cache=FakeStickyRouteCache(),
    )

    monkeypatch.setattr(
        "stackowl.pipeline.turn_persist.get_services", lambda: services
    )

    state = PipelineState(
        trace_id="trace-z", session_key="sess-brain", input_text="Yes review",
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
        def evict(self, session_key):
            evicted.append(session_key)

    services = StepServices(
        retry_queue_store=None,
        sticky_route_cache=FakeStickyRouteCache(),
    )

    monkeypatch.setattr(
        "stackowl.pipeline.turn_persist.get_services", lambda: services
    )

    state = PipelineState(
        trace_id="trace-ok", session_key="sess-brain", input_text="thanks!",
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

    services = StepServices(
        retry_queue_store=FakeRetryQueueStore(),
    )

    monkeypatch.setattr(
        "stackowl.pipeline.turn_persist.get_services", lambda: services
    )

    state = PipelineState(
        trace_id="retry-x", session_key="sess-x", input_text="prepare me for the interview",
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
