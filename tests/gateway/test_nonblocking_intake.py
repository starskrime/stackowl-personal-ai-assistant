"""Non-blocking in-chat intake (concurrent-msg §4.3, Task 5).

A new same-session message is accepted INSTANTLY: instead of blocking on the
running turn (the deleted ``serialize_prior``), the gateway registers the turn in
the :class:`TurnRegistry` and — if one is already running — enqueues the new one
FIFO + acks immediately. On the running turn's completion the next queued intake
is popped and dispatched. Cross-session stays fully parallel.

These drive the REAL :class:`TurnRegistry` plus a tiny dispatcher that mirrors the
orchestrator's intake decision, so the queue/drain semantics are exercised end to
end. Every await is bounded by ``asyncio.wait_for`` so a hang FAILS, never wedges.
"""

from __future__ import annotations

import asyncio

import pytest

from stackowl.gateway.turn_registry import PendingIntake, TurnRegistry
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps import deliver as deliver_step
from stackowl.pipeline.streaming import ResponseChunk, StreamRegistry

pytestmark = pytest.mark.asyncio


# --------------------------------------------------------------------------- a)
async def test_midturn_same_session_enqueues_without_blocking() -> None:
    """A mid-turn same-session message does NOT await the running turn — it enqueues."""
    reg = TurnRegistry()
    gate = asyncio.Event()

    async def slow_turn() -> None:
        await gate.wait()

    t = asyncio.create_task(slow_turn())
    await reg.register("req-1", session_id="s1", task=t, target=None, original_input="first")

    # A second message arrives while req-1 runs — intake must NOT block on it.
    assert reg.running("s1") is not None
    reg.enqueue("s1", original_input="second", request_id="req-2", target=None)
    # Intake returned immediately; the running turn is still in flight.
    assert not t.done()

    # Release the running turn, then drain FIFO.
    gate.set()
    await asyncio.wait_for(t, 1.0)
    await reg.deregister("req-1")
    nxt = reg.pop_next("s1")
    assert nxt is not None and nxt.original_input == "second"


# --------------------------------------------------------------------------- a')
async def test_fifo_drain_after_completion() -> None:
    """A real completion -> pop_next dispatch loop honours FIFO order across a queue."""
    reg = TurnRegistry()
    dispatched: list[str] = []

    async def _dispatch(intake: PendingIntake) -> None:
        # Mirror the orchestrator: register the popped intake as the new running
        # turn, run it, then drain the next.
        done = asyncio.Event()

        async def _run() -> None:
            done.set()

        task = asyncio.create_task(_run())
        await reg.register(
            intake.request_id, session_id="s1", task=task,
            target=intake.target, original_input=intake.original_input,
        )
        dispatched.append(intake.original_input)
        await asyncio.wait_for(task, 1.0)
        await reg.deregister(intake.request_id)
        nxt = reg.pop_next("s1")
        if nxt is not None:
            await _dispatch(nxt)

    # First turn runs; two more queue behind it.
    gate = asyncio.Event()

    async def first() -> None:
        await gate.wait()

    t0 = asyncio.create_task(first())
    await reg.register("req-0", session_id="s1", task=t0, target=None, original_input="zero")
    reg.enqueue("s1", original_input="one", request_id="req-1", target=None)
    reg.enqueue("s1", original_input="two", request_id="req-2", target=None)

    gate.set()
    await asyncio.wait_for(t0, 1.0)
    await reg.deregister("req-0")
    nxt = reg.pop_next("s1")
    assert nxt is not None
    await asyncio.wait_for(_dispatch(nxt), 2.0)

    assert dispatched == ["one", "two"]  # FIFO
    assert reg.running("s1") is None  # queue fully drained


# --------------------------------------------------------------------------- b)
async def test_cross_session_runs_concurrently() -> None:
    """Two different-session turns run concurrently — neither waits on the other."""
    reg = TurnRegistry()
    a_started = asyncio.Event()
    b_started = asyncio.Event()
    release = asyncio.Event()

    async def turn(started: asyncio.Event) -> None:
        started.set()
        await release.wait()

    ta = asyncio.create_task(turn(a_started))
    tb = asyncio.create_task(turn(b_started))
    await reg.register("req-a", session_id="sA", task=ta, target=None, original_input="a")
    await reg.register("req-b", session_id="sB", task=tb, target=None, original_input="b")

    # BOTH sessions have a running turn at the same time (no serialization).
    assert reg.running("sA") is not None
    assert reg.running("sB") is not None
    await asyncio.wait_for(a_started.wait(), 1.0)
    await asyncio.wait_for(b_started.wait(), 1.0)
    # Neither is queued behind the other.
    assert reg.pop_next("sA") is None
    assert reg.pop_next("sB") is None

    release.set()
    await asyncio.wait_for(asyncio.gather(ta, tb), 1.0)


# --------------------------------------------------------------------------- c)
async def test_deliver_stamps_reply_target_from_state() -> None:
    """A Telegram-origin state (reply_target=123) -> delivered chunks carry target=123."""
    stream_registry = StreamRegistry()
    trace_id = "tg-trace-1"
    writer, reader = stream_registry.create(trace_id)

    state = PipelineState(
        trace_id=trace_id,
        session_id="telegram:123",
        input_text="hi",
        channel="telegram",
        owl_name="secretary",
        pipeline_step="deliver",
        interactive=True,
        reply_target=123,
        responses=(
            ResponseChunk(
                content="hello", is_final=False, chunk_index=0,
                trace_id=trace_id, owl_name="secretary",
            ),
        ),
    )

    # deliver pulls the registry from pipeline services context.
    from stackowl.pipeline.services import StepServices, reset_services, set_services

    token = set_services(StepServices(stream_registry=stream_registry))
    try:
        await asyncio.wait_for(deliver_step.run(state), 1.0)
    finally:
        reset_services(token)

    delivered: list[ResponseChunk] = []
    async for chunk in reader:
        delivered.append(chunk)

    assert len(delivered) == 1
    assert delivered[0].content == "hello"
    assert delivered[0].target == 123  # stamped from state.reply_target


async def test_deliver_leaves_target_none_for_cli() -> None:
    """A CLI-origin state (reply_target=None) -> chunks carry target=None."""
    stream_registry = StreamRegistry()
    trace_id = "cli-trace-1"
    writer, reader = stream_registry.create(trace_id)

    state = PipelineState(
        trace_id=trace_id,
        session_id="cli",
        input_text="hi",
        channel="cli",
        owl_name="secretary",
        pipeline_step="deliver",
        interactive=True,
        reply_target=None,
        responses=(
            ResponseChunk(
                content="hello", is_final=False, chunk_index=0,
                trace_id=trace_id, owl_name="secretary",
            ),
        ),
    )

    from stackowl.pipeline.services import StepServices, reset_services, set_services

    token = set_services(StepServices(stream_registry=stream_registry))
    try:
        await asyncio.wait_for(deliver_step.run(state), 1.0)
    finally:
        reset_services(token)

    delivered: list[ResponseChunk] = []
    async for chunk in reader:
        delivered.append(chunk)

    assert len(delivered) == 1
    assert delivered[0].target is None
