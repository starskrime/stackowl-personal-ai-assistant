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


def test_default_global_running_max_is_host_derived() -> None:
    # Derived from os.cpu_count(); never a Jetson-pinned constant. Always >= 1.
    assert default_global_running_max() >= 1


@pytest.mark.asyncio
async def test_default_ctor_uses_host_probe() -> None:
    reg = TurnRegistry()
    # default global cap sized from host, not a hardcoded magic number
    assert reg.global_running_max == default_global_running_max()
