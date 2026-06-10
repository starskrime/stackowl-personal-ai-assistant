from __future__ import annotations

import asyncio

import pytest

from stackowl.gateway.turn_registry import Turn, TurnRegistry, TurnStatus


@pytest.mark.asyncio
async def test_status_cas_is_one_way() -> None:
    reg = TurnRegistry()
    task = asyncio.create_task(asyncio.sleep(0))
    turn = await reg.register("req-1", session_id="s1", task=task, target=None, original_input="hi")
    assert turn.status is TurnStatus.RUNNING
    assert await reg.cas_status("req-1", TurnStatus.RUNNING, TurnStatus.FINALIZING) is True
    # backward / skip transitions rejected
    assert await reg.cas_status("req-1", TurnStatus.RUNNING, TurnStatus.DONE) is False
    assert await reg.cas_status("req-1", TurnStatus.FINALIZING, TurnStatus.DONE) is True
    await task


@pytest.mark.asyncio
async def test_one_running_per_session_plus_fifo_queue() -> None:
    reg = TurnRegistry()
    t = asyncio.create_task(asyncio.sleep(0))
    await reg.register("req-1", session_id="s1", task=t, target=None, original_input="a")
    assert reg.running("s1") is not None
    reg.enqueue("s1", original_input="b", request_id="req-2", target=None)
    reg.enqueue("s1", original_input="c", request_id="req-3", target=None)
    first = reg.pop_next("s1")
    assert first is not None and first.original_input == "b"
    second = reg.pop_next("s1")
    assert second is not None and second.original_input == "c"  # FIFO
    assert reg.pop_next("s1") is None
    await t


@pytest.mark.asyncio
async def test_deregister_clears_running() -> None:
    reg = TurnRegistry()
    t = asyncio.create_task(asyncio.sleep(0))
    await reg.register("req-1", session_id="s1", task=t, target=None, original_input="a")
    await reg.deregister("req-1")
    assert reg.running("s1") is None
    assert reg.get("req-1") is None
    await t


@pytest.mark.asyncio
async def test_sweeper_snapshots_then_acts_and_reaps_done_without_status() -> None:
    reg = TurnRegistry()

    async def quick() -> None:
        return None

    t = asyncio.create_task(quick())
    await reg.register("req-1", session_id="s1", task=t, target=None, original_input="a")
    await t  # task done, status still RUNNING (lost the finally race)
    reaped = await reg.sweep(ttl_seconds=0.0)
    assert "req-1" in reaped
    assert reg.get("req-1") is None


@pytest.mark.asyncio
async def test_steering_mailbox_is_bounded() -> None:
    reg = TurnRegistry()
    t = asyncio.create_task(asyncio.sleep(0))
    turn: Turn = await reg.register("req-1", session_id="s1", task=t, target=None, original_input="a")
    assert isinstance(turn.steering_mailbox, asyncio.Queue)
    assert turn.steering_mailbox.maxsize == 8
    assert turn.stop_requested is False
    await t
