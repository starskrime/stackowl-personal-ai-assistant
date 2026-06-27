"""F-67 — a reaped WEDGED (was_running) turn re-enqueues its own goal, bounded.

Today ``sweep`` reaps a wedged turn (task done but status never reached DONE, or
expired) and ``deregister``s it — its ``original_input`` is discarded and the
user's goal is silently lost. The post-reap hooks only free the slot / evict the
parked raw message; neither re-dispatches the wedged turn's OWN goal.

Fix: on reap of a turn whose ``_running`` slot it held (the wedge case), the
registry re-enqueues ``turn.original_input`` as a fresh queued-new intake (reusing
the existing ``enqueue`` + ``pop_next`` machinery the orchestrator already drains),
ONCE — guarded by a ``redispatch_count`` so a genuinely-poisonous turn that keeps
wedging cannot loop forever.
"""

from __future__ import annotations

import asyncio

import pytest

from stackowl.gateway.turn_registry import TurnRegistry, TurnStatus


async def _done_task() -> asyncio.Task[None]:
    async def _noop() -> None:
        return None

    t: asyncio.Task[None] = asyncio.create_task(_noop())
    await t
    return t


@pytest.mark.asyncio
async def test_reaped_wedged_turn_re_enqueues_original_input_once() -> None:
    reg = TurnRegistry()
    task = await _done_task()
    turn = await reg.register(
        "wedged-1", session_id="s1", task=task, target=99, original_input="do the thing"
    )
    assert turn.status is not TurnStatus.DONE  # the wedge shape

    reaped = await reg.sweep(ttl_seconds=0.0)
    assert "wedged-1" in reaped

    # The wedged turn's OWN goal is back on the session's intake queue, not lost.
    nxt = reg.pop_next("s1")
    assert nxt is not None
    assert nxt.original_input == "do the thing"
    assert nxt.target == 99
    # A distinct request_id so the orchestrator's queued-new dispatch keys it uniquely.
    assert nxt.request_id != "wedged-1"


@pytest.mark.asyncio
async def test_normally_completed_turn_is_not_re_enqueued() -> None:
    """A turn that reached DONE before reap must NOT be re-dispatched."""
    reg = TurnRegistry()
    task = await _done_task()
    await reg.register(
        "ok-1", session_id="s1", task=task, target=None, original_input="finished work"
    )
    # Drive it to DONE the legal way (RUNNING->FINALIZING->DONE).
    assert await reg.cas_status("ok-1", TurnStatus.RUNNING, TurnStatus.FINALIZING)
    assert await reg.cas_status("ok-1", TurnStatus.FINALIZING, TurnStatus.DONE)

    reaped = await reg.sweep(ttl_seconds=0.0)
    assert "ok-1" in reaped  # done turns are still reclaimed from the table...
    assert reg.pop_next("s1") is None  # ...but their goal is NOT re-enqueued


@pytest.mark.asyncio
async def test_re_dispatch_is_bounded_so_a_poisonous_turn_cannot_loop() -> None:
    """A turn that keeps wedging is re-dispatched at most a bounded number of times."""
    reg = TurnRegistry()
    rid = "poison-0"
    text = "the cursed request"
    target = 7

    seen = 0
    for _ in range(10):
        task = await _done_task()
        await reg.register(
            rid, session_id="s1", task=task, target=target, original_input=text
        )
        reaped = await reg.sweep(ttl_seconds=0.0)
        assert rid in reaped
        nxt = reg.pop_next("s1")
        if nxt is None:
            break
        # Simulate the orchestrator picking the queued-new intake back up as a
        # fresh running turn that wedges again, carrying the re-dispatch lineage.
        seen += 1
        rid = nxt.request_id
        text = nxt.original_input
        target = nxt.target  # type: ignore[assignment]

    # Bounded: it re-dispatched a small finite number of times, then gave up.
    assert 1 <= seen <= 3
