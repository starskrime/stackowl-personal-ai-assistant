import pytest

from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import ResponseChunk
from stackowl.pipeline.turn_persist import persist_turn


@pytest.mark.asyncio
async def test_a_floored_turn_makes_persist_turn_enqueue_NOTHING(monkeypatch):
    """A floored turn retries on ITS OWN row — persist_turn produces no row at all.

    REWRITTEN TWICE, and both rewrites are recorded because the invariant outlived
    two owners.

      v1 asserted an insert into `retry_queue` — a SECOND engine holding nothing
        `tasks` does not already hold, whose own docstring said it re-armed
        "forever — no attempt cap, no terminal give-up". That unbounded policy is
        what messaged Bakir for hours from 5,766 rows.
      v2 asserted persist_turn enqueuing a `retry-<trace_id>` TASK. Bounded, but
        still a SECOND PRODUCER for one turn: born `pending` with no lease, so the
        loop could claim it while the fast path was still delivering. Measured on
        trace e6c1d3e1 — floor sent 17:25:40, the loop's answer arrived 17:26:01 as
        a second message. Bakir: "it always sends failed request first then after
        some time I am getting final answer."

    v3, here: persist_turn enqueues NOTHING. The floor is an unachieved goal, and
    the platform already knows how to express that — `fail_and_requeue` on the row
    that already exists, with the ceiling and dead-letter escalation it already has.
    The retry contract (trace, session, goal, boundedness) is asserted against its
    new owner in tests/pipeline/test_a_floored_turn_retries_on_the_ONE_loop.py.
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

    assert not enqueued, (
        f"persist_turn minted {len(enqueued)} durable row(s) for a floored turn. "
        "One turn, two producers — on Telegram the user sees two replies to one "
        "question (trace e6c1d3e1)."
    )


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
