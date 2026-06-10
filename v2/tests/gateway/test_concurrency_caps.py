from __future__ import annotations

import asyncio

import pytest

from stackowl.gateway.turn_registry import QueueFull, TurnRegistry, default_global_running_max


@pytest.mark.asyncio
async def test_per_session_queue_bounded() -> None:
    reg = TurnRegistry(per_session_queue_max=2, global_running_max=100)
    t = asyncio.create_task(asyncio.sleep(0))
    await reg.register("r0", session_id="s1", task=t, target=None, original_input="x")
    reg.enqueue("s1", original_input="a", request_id="r1", target=None)
    reg.enqueue("s1", original_input="b", request_id="r2", target=None)
    with pytest.raises(QueueFull):
        reg.enqueue("s1", original_input="c", request_id="r3", target=None)
    await t


@pytest.mark.asyncio
async def test_global_running_cap(monkeypatch) -> None:
    reg = TurnRegistry(per_session_queue_max=8, global_running_max=1)
    t = asyncio.create_task(asyncio.sleep(0))
    await reg.register("r0", session_id="s1", task=t, target=None, original_input="x")
    assert reg.at_global_capacity() is True  # second session must wait/queue
    await t


@pytest.mark.asyncio
async def test_not_at_global_capacity_when_below_max() -> None:
    reg = TurnRegistry(per_session_queue_max=8, global_running_max=2)
    t = asyncio.create_task(asyncio.sleep(0))
    await reg.register("r0", session_id="s1", task=t, target=None, original_input="x")
    assert reg.at_global_capacity() is False
    await t


@pytest.mark.asyncio
async def test_capacity_frees_after_deregister() -> None:
    reg = TurnRegistry(per_session_queue_max=8, global_running_max=1)
    t = asyncio.create_task(asyncio.sleep(0))
    await reg.register("r0", session_id="s1", task=t, target=None, original_input="x")
    assert reg.at_global_capacity() is True
    await reg.deregister("r0")
    assert reg.at_global_capacity() is False
    await t


@pytest.mark.asyncio
async def test_idle_queued_session_surfaces_global_cap_holders() -> None:
    """idle_queued_session() returns a session with a queued intake but no running turn.

    This is the global-cap WAKE seam: a turn held because the host was at the
    global cap is enqueued on its own idle session; the registry must surface that
    session so the orchestrator can dispatch it when capacity frees.
    """
    reg = TurnRegistry(per_session_queue_max=8, global_running_max=4)
    # sA is RUNNING with a queued follow-up -> NOT idle, must not be surfaced.
    t = asyncio.create_task(asyncio.sleep(0))
    await reg.register("rA", session_id="sA", task=t, target=None, original_input="a")
    reg.enqueue("sA", original_input="a2", request_id="rA2", target=None)
    assert reg.idle_queued_session() is None  # sA is running

    # sB is IDLE with a held intake -> the holder the wake must surface.
    reg.enqueue("sB", original_input="b", request_id="rB", target=None)
    assert reg.idle_queued_session() == "sB"

    # Once sB starts running it is no longer surfaced.
    reg.pop_next("sB")
    t2 = asyncio.create_task(asyncio.sleep(0))
    await reg.register("rB", session_id="sB", task=t2, target=None, original_input="b")
    assert reg.idle_queued_session() is None
    await t
    await t2


def test_default_global_running_max_is_host_derived() -> None:
    # Derived from os.cpu_count(); never a Jetson-pinned constant. Always >= 1.
    assert default_global_running_max() >= 1


@pytest.mark.asyncio
async def test_default_ctor_uses_host_probe() -> None:
    reg = TurnRegistry()
    # default global cap sized from host, not a hardcoded magic number
    assert reg.global_running_max == default_global_running_max()
