from __future__ import annotations

import asyncio

import pytest

from stackowl.gateway.turn_registry import TurnRegistry


@pytest.mark.asyncio
async def test_mailbox_bounded_and_coalesces_under_spam() -> None:
    reg = TurnRegistry()
    t = asyncio.create_task(asyncio.sleep(0))
    turn = await reg.register("r1", session_id="s1", task=t, target=None, original_input="x")
    for i in range(50):
        reg.put_steer("r1", f"steer-{i}")  # bounded + supersede-oldest
    assert turn.steering_mailbox.qsize() <= turn.steering_mailbox.maxsize
    # the newest steers survive (oldest superseded)
    drained = []
    while not turn.steering_mailbox.empty():
        drained.append(turn.steering_mailbox.get_nowait())
    assert "steer-49" in drained
    await t


@pytest.mark.asyncio
async def test_put_steer_unknown_request_is_noop() -> None:
    reg = TurnRegistry()
    # No registered turn for "ghost" — must be a fail-safe no-op, never raise.
    reg.put_steer("ghost", "steer-x")


@pytest.mark.asyncio
async def test_full_mailbox_supersede_drops_only_oldest() -> None:
    reg = TurnRegistry()
    t = asyncio.create_task(asyncio.sleep(0))
    turn = await reg.register("r2", session_id="s2", task=t, target=None, original_input="x")
    maxsize = turn.steering_mailbox.maxsize
    total = maxsize + 5
    for i in range(total):
        reg.put_steer("r2", f"s-{i}")
    drained = []
    while not turn.steering_mailbox.empty():
        drained.append(turn.steering_mailbox.get_nowait())
    # bounded: exactly maxsize retained
    assert len(drained) == maxsize
    # the newest `maxsize` survive, the oldest were superseded
    expected = [f"s-{i}" for i in range(total - maxsize, total)]
    assert drained == expected
    await t


@pytest.mark.asyncio
async def test_try_steer_running_supersedes_on_full_mailbox() -> None:
    """try_steer's RUNNING branch supersedes-oldest on a FULL mailbox (§5.4).

    Per §5.4, a steer at a RUNNING turn with a full mailbox drops the oldest
    pending steer and accepts the newest — it does NOT fall to NEW (that fallback
    is for FINALIZING/DONE). Each call still returns "STEER".
    """
    reg = TurnRegistry()
    t = asyncio.create_task(asyncio.sleep(0))
    turn = await reg.register("r3", session_id="s3", task=t, target=None, original_input="x")
    maxsize = turn.steering_mailbox.maxsize
    for i in range(maxsize + 10):
        verdict = await reg.try_steer(
            "r3", f"st-{i}", session_id="s3", request_id_new=f"new-{i}", target=None
        )
        assert verdict == "STEER"  # RUNNING always accepts (supersede, never NEW)
    assert turn.steering_mailbox.qsize() == maxsize
    drained = []
    while not turn.steering_mailbox.empty():
        drained.append(turn.steering_mailbox.get_nowait())
    # newest retained
    last = maxsize + 10 - 1
    assert f"st-{last}" in drained
    await t
