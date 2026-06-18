from __future__ import annotations

import asyncio

import pytest

from stackowl.gateway.turn_registry import TurnRegistry
from stackowl.pipeline.steps.execute import make_steering_callback
from stackowl.providers.react_callback import ReActIterationState


@pytest.mark.asyncio
async def test_steering_drain_folds_pending_message() -> None:
    reg = TurnRegistry()
    t = asyncio.create_task(asyncio.sleep(0))
    turn = await reg.register(
        "req-1", session_id="s1", task=t, target=None, original_input="research X"
    )
    turn.steering_mailbox.put_nowait("also include Y")
    cb = make_steering_callback(reg, "req-1")
    folded = await cb(ReActIterationState(iteration=0, messages=[], tool_call_records=[]))
    assert folded is not None
    assert "[steering]" in folded[0]["content"] and "include Y" in folded[0]["content"]
    # empty mailbox -> None, and never blocks on await get()
    assert (
        await asyncio.wait_for(
            cb(ReActIterationState(iteration=1, messages=[], tool_call_records=[])),
            timeout=1.0,
        )
        is None
    )
    await t


@pytest.mark.asyncio
async def test_steering_coalesces_multiple_pending() -> None:
    reg = TurnRegistry()
    t = asyncio.create_task(asyncio.sleep(0))
    turn = await reg.register(
        "req-2", session_id="s2", task=t, target=None, original_input="do A"
    )
    turn.steering_mailbox.put_nowait("first hint")
    turn.steering_mailbox.put_nowait("second hint")
    cb = make_steering_callback(reg, "req-2")
    folded = await cb(ReActIterationState(iteration=0, messages=[], tool_call_records=[]))
    assert folded is not None
    assert len(folded) == 1
    assert folded[0]["role"] == "user"
    content = folded[0]["content"]
    assert content.startswith("[steering] ")
    assert "first hint" in content and "second hint" in content
    # mailbox drained to empty
    assert turn.steering_mailbox.empty()
    await t


@pytest.mark.asyncio
async def test_steering_no_registry_returns_no_callback() -> None:
    # Fail-safe: no TurnRegistry wired → no callback at all (None), so the default
    # provider call stays byte-for-byte unchanged (no on_iteration_complete kwarg).
    assert make_steering_callback(None, "any-req") is None


@pytest.mark.asyncio
async def test_steering_no_registered_turn_returns_none() -> None:
    reg = TurnRegistry()
    cb = make_steering_callback(reg, "missing-req")
    folded = await asyncio.wait_for(
        cb(ReActIterationState(iteration=0, messages=[], tool_call_records=[])),
        timeout=1.0,
    )
    assert folded is None


@pytest.mark.asyncio
async def test_steering_empty_mailbox_returns_none() -> None:
    reg = TurnRegistry()
    t = asyncio.create_task(asyncio.sleep(0))
    await reg.register(
        "req-3", session_id="s3", task=t, target=None, original_input="task"
    )
    cb = make_steering_callback(reg, "req-3")
    folded = await asyncio.wait_for(
        cb(ReActIterationState(iteration=0, messages=[], tool_call_records=[])),
        timeout=1.0,
    )
    assert folded is None
    await t
