"""Tests for A2ADelegator.delegate() fidelity — governor-decided A2AResult (T3).

Covers:
* timeout path returns A2AResult(status="timeout"), not an empty string
* child-error path (sub-pipeline raises StackOwlError) returns A2AResult(status="child_error")
* success path returns A2AResult(status="ok") with correct content
* empty response returns A2AResult(status="empty")
* delegation_chain is stamped on the child sub_state
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from stackowl.messaging.a2a import A2AQueue
from stackowl.owls.a2a_delegation import A2ADelegator, A2AResult
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState


def _parent(**kw: Any) -> PipelineState:
    return PipelineState(
        trace_id="t",
        session_id="s",
        input_text="go",
        channel="cli",
        owl_name="secretary",
        pipeline_step="dispatch",
        **kw,
    )


# ------------------------------------------------------------------ timeout path


@pytest.mark.asyncio
async def test_timeout_returns_a2aresult_timeout_not_empty_string() -> None:
    """A2ATimeoutError yields A2AResult(status='timeout'), not bare ''.

    _run_specialist is patched to block forever so it never posts a reply,
    forcing the receive() side to time out.
    """
    q = A2AQueue()
    deleg = A2ADelegator(a2a_queue=q, services=StepServices(), timeout_seconds=0.05)

    async def _blocking(*args: Any, **kwargs: Any) -> None:
        await asyncio.sleep(60)  # never completes within the test

    with patch.object(deleg, "_run_specialist", new=_blocking):
        res = await deleg.delegate(
            from_owl="secretary", to_owl="ghost", sub_task="x", parent_state=_parent()
        )
    assert isinstance(res, A2AResult)
    assert res.status == "timeout"
    assert res.content == ""
    assert res.resolved_owl == "ghost"


# ----------------------------------------------------------------- child-error path


@pytest.mark.asyncio
async def test_child_error_receive_returns_a2aresult_child_error() -> None:
    """StackOwlError on receive yields A2AResult(status='child_error')."""
    from stackowl.exceptions import StackOwlError

    q = A2AQueue()
    deleg = A2ADelegator(a2a_queue=q, services=StepServices(), timeout_seconds=5.0)

    # Patch receive to raise StackOwlError immediately.
    with patch.object(q, "receive", side_effect=StackOwlError("bang")):
        res = await deleg.delegate(
            from_owl="secretary", to_owl="broken", sub_task="y", parent_state=_parent()
        )

    assert isinstance(res, A2AResult)
    assert res.status == "child_error"
    assert res.content == ""
    assert res.resolved_owl == "broken"
    assert "bang" in res.child_detail


# ----------------------------------------------------------------- success path


@pytest.mark.asyncio
async def test_success_returns_a2aresult_ok_with_content() -> None:
    """Happy path: A2AResult(status='ok', content=<text>)."""
    from stackowl.messaging.a2a import A2AMessage

    q = A2AQueue()
    deleg = A2ADelegator(a2a_queue=q, services=StepServices(), timeout_seconds=5.0)

    # Build a pre-canned response message.
    reply = A2AMessage.now(
        from_owl="scout",
        to_owl="secretary",
        content="the answer",
        message_type="response",
        trace_id="t",
        status="ok",
        error=None,
    )

    # Patch _run_specialist to a no-op (it would fail without a real backend)
    # and patch receive to return our canned reply immediately.
    with (
        patch.object(deleg, "_run_specialist", new=AsyncMock(return_value=None)),
        patch.object(q, "receive", new=AsyncMock(return_value=reply)),
    ):
        res = await deleg.delegate(
            from_owl="secretary", to_owl="scout", sub_task="do something", parent_state=_parent()
        )

    assert isinstance(res, A2AResult)
    assert res.status == "ok"
    assert res.content == "the answer"
    assert res.resolved_owl == "scout"


# ----------------------------------------------------------------- empty response


@pytest.mark.asyncio
async def test_empty_response_returns_a2aresult_empty() -> None:
    """A response with blank content yields A2AResult(status='empty')."""
    from stackowl.messaging.a2a import A2AMessage

    q = A2AQueue()
    deleg = A2ADelegator(a2a_queue=q, services=StepServices(), timeout_seconds=5.0)

    reply = A2AMessage.now(
        from_owl="scout",
        to_owl="secretary",
        content="   ",  # whitespace-only → empty
        message_type="response",
        trace_id="t",
        status=None,
        error=None,
    )

    with (
        patch.object(deleg, "_run_specialist", new=AsyncMock(return_value=None)),
        patch.object(q, "receive", new=AsyncMock(return_value=reply)),
    ):
        res = await deleg.delegate(
            from_owl="secretary", to_owl="scout", sub_task="noop", parent_state=_parent()
        )

    assert isinstance(res, A2AResult)
    assert res.status == "empty"
    assert res.resolved_owl == "scout"


# ------------------------------------------------------------ delegation_chain stamp


@pytest.mark.asyncio
async def test_delegation_chain_stamped_on_child_sub_state() -> None:
    """_run_specialist stamps delegation_chain=(to_owl,) into the child sub_state."""
    from stackowl.exceptions import StackOwlError
    from stackowl.messaging.a2a import A2AMessage

    q = A2AQueue()
    deleg = A2ADelegator(a2a_queue=q, services=StepServices(), timeout_seconds=5.0)

    captured: list[PipelineState] = []

    # Intercept _run_under_governor to capture sub_state before the real backend runs.
    async def _fake_governor(backend: Any, sub_state: PipelineState) -> PipelineState:
        captured.append(sub_state)
        # Return a minimal final_state (no responses, no errors).
        return sub_state.evolve(responses=(), errors=())

    reply = A2AMessage.now(
        from_owl="scout",
        to_owl="secretary",
        content="done",
        message_type="response",
        trace_id="t",
        status="ok",
        error=None,
    )

    with (
        patch.object(deleg, "_run_under_governor", new=_fake_governor),
        patch.object(q, "receive", new=AsyncMock(return_value=reply)),
    ):
        parent = _parent(delegation_chain=("root",))
        await deleg.delegate(
            from_owl="secretary", to_owl="scout", sub_task="task", parent_state=parent
        )

    assert len(captured) == 1
    child_state = captured[0]
    assert child_state.delegation_chain == ("root", "scout"), child_state.delegation_chain
