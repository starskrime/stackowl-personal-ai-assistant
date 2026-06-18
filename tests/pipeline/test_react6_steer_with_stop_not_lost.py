"""REACT-6 / F033 — a steer sent alongside a stop is never silently eaten.

The cooperative-stop callback drains the steering mailbox at the iteration
boundary and THEN honors the stop flag. Previously the drained steers were
discarded ("the turn is stopping, nothing to fold them into") — but the callback
had already removed them from the mailbox, so the completion-seam survivor
re-route (``finalize_and_drain``) found an empty mailbox and the user's message
was lost for good.

The fix carries the drained steers on ``TurnStopped`` and the execute finalize
seam re-routes them as queued-new turns (the SAME path survivors take), so a
steer co-arriving with a stop is preserved as the next turn — never lost.
"""
from __future__ import annotations

import asyncio

import pytest

from stackowl.exceptions import TurnStopped
from stackowl.gateway.turn_registry import TurnRegistry
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps.execute import _run_with_tools, make_steering_callback
from stackowl.providers.react_callback import ReActIterationState

# --------------------------------------------------------------------------- #
# Unit: TurnStopped carries the drained steers it could not fold.
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_callback_carries_drained_steers_on_stop() -> None:
    reg = TurnRegistry()
    bg = asyncio.create_task(asyncio.sleep(0))
    await reg.register("r1", session_id="s1", task=bg, target=None, original_input="x")
    turn = reg.get("r1")
    assert turn is not None
    turn.steering_mailbox.put_nowait("also check the logs")  # a co-arriving steer
    reg.request_stop("r1")

    cb = make_steering_callback(reg, "r1")
    assert cb is not None

    with pytest.raises(TurnStopped) as ei:
        await cb(ReActIterationState(iteration=0, messages=[], tool_call_records=[]))

    assert ei.value.drained_steers == ["also check the logs"], (
        "the steer drained at the stop boundary must ride on TurnStopped, not vanish"
    )
    await bg


# --------------------------------------------------------------------------- #
# Registry re-route: drained steers become queued-new turns.
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_registry_requeues_drained_steers_as_new() -> None:
    reg = TurnRegistry()
    bg = asyncio.create_task(asyncio.sleep(0))
    await reg.register("r1", session_id="s1", task=bg, target=None, original_input="x")

    await reg.requeue_steers_as_new("r1", ["follow up please"])

    drained: list[str] = []
    while (item := reg.pop_next("s1")) is not None:
        drained.append(item.original_input)
    assert "follow up please" in drained, (
        "the drained steer must be re-enqueued as a queued-new turn"
    )
    await bg


# --------------------------------------------------------------------------- #
# End-to-end through the real execute finalize seam.
# --------------------------------------------------------------------------- #

class _StopAfterToolProvider:
    """One tool round, then the steering callback honors a pre-set stop flag."""

    protocol = "anthropic"

    async def complete_with_tools(  # noqa: ANN001
        self,
        *,
        user_text: str,
        system_text: str,
        tool_schemas: list[dict[str, object]],
        tool_dispatcher,
        history=None,
        on_iteration_complete=None,
        **_kwargs: object,
    ):
        if on_iteration_complete is not None:
            # The boundary callback raises TurnStopped (stop flag is set).
            await on_iteration_complete(
                ReActIterationState(iteration=0, messages=[
                    {"role": "assistant", "content": "partial work"},
                ], tool_call_records=[])
            )
        return ("unreached", [])


@pytest.mark.asyncio
async def test_execute_reroutes_steer_on_stop_end_to_end() -> None:
    from stackowl.tools.registry import ToolRegistry

    reg = TurnRegistry()
    bg = asyncio.create_task(asyncio.sleep(0))
    request_id = "trace-stop-steer"
    await reg.register(
        request_id, session_id="s1", task=bg, target=None, original_input="research X"
    )
    turn = reg.get(request_id)
    assert turn is not None
    turn.steering_mailbox.put_nowait("and summarize it for me")
    reg.request_stop(request_id)

    tool_reg = ToolRegistry.with_defaults()
    token = set_services(StepServices(tool_registry=tool_reg, turn_registry=reg))
    try:
        state = PipelineState(
            trace_id=request_id, session_id="s1", input_text="research X",
            channel="cli", owl_name="default", pipeline_step="execute",
            interactive=True,
        )
        out = await asyncio.wait_for(
            _run_with_tools(state, _StopAfterToolProvider(), tool_reg),  # type: ignore[arg-type]
            timeout=5.0,
        )
    finally:
        reset_services(token)

    # Graceful stop chunk produced.
    assert out.responses
    assert "stop" in out.responses[-1].content.casefold()
    # The co-arriving steer was NOT lost — it is now a queued-new turn.
    drained: list[str] = []
    while (item := reg.pop_next("s1")) is not None:
        drained.append(item.original_input)
    assert "and summarize it for me" in drained, (
        "the steer co-arriving with the stop must survive as a queued-new turn"
    )
    await bg
