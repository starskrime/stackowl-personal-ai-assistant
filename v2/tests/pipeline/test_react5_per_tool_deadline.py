"""REACT-5 / F061 — /stop latency is bounded even mid-long in-flight tool.

Cooperative stop is honored at the ReAct ITERATION boundary, so a long-running
tool could delay a stop by its full (unbounded) duration. REACT-5 adds a per-tool
execution DEADLINE that cancels the TOOL's own awaitable (via asyncio.wait_for,
NOT task.cancel() on the turn) so:

  * a hung/long tool can never block the loop — and therefore a stop — for longer
    than the per-tool deadline, and
  * once a stop is already requested, a not-yet-started tool is short-circuited
    immediately (near-zero added latency).

The bound is the documented max-tool-time. Tests drive the REAL _run_with_tools
dispatch seam with a deliberately-hanging tool and a tiny deadline override.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from stackowl.gateway.turn_registry import TurnRegistry
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps import execute as execute_mod
from stackowl.pipeline.steps.execute import _run_with_tools
from stackowl.providers.react_callback import ReActIterationState
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.registry import ToolRegistry


class _HangingTool(Tool):
    """A read-severity tool that hangs far longer than the per-tool deadline."""

    def __init__(self) -> None:
        self.cancelled = False

    @property
    def name(self) -> str:
        return "hang_tool"

    @property
    def description(self) -> str:
        return "Hangs forever (deadline test)."

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {}}

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name="hang_tool", description=self.description,
            parameters=self.parameters, action_severity="read",
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        try:
            await asyncio.sleep(30)  # >> the deadline override
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return ToolResult(success=True, output="never", duration_ms=1.0)


class _OneToolThenDoneProvider:
    """Issue ONE tool call (the hanging tool), observe it, then draft an answer."""

    protocol = "anthropic"

    def __init__(self) -> None:
        self.tool_results: list[str] = []

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
        result = await tool_dispatcher("hang_tool", {})
        self.tool_results.append(result)
        if on_iteration_complete is not None:
            await on_iteration_complete(
                ReActIterationState(iteration=0, messages=[
                    {"role": "assistant", "content": "done"},
                ], tool_call_records=[])
            )
        return ("final answer after the tool was bounded", [])


def _state(request_id: str) -> PipelineState:
    return PipelineState(
        trace_id=request_id, session_id="s1", input_text="run the hanging tool",
        channel="cli", owl_name="default", pipeline_step="execute", interactive=True,
    )


@pytest.mark.asyncio
async def test_hung_tool_is_bounded_by_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    """A hanging tool is cancelled at the per-tool deadline (its OWN awaitable),
    the loop continues, and the turn finishes well within the bound — the turn
    task is never cancelled."""
    monkeypatch.setattr(execute_mod, "_TOOL_DEADLINE_S", 0.2)

    tool = _HangingTool()
    tool_reg = ToolRegistry()
    tool_reg.register(tool)
    provider = _OneToolThenDoneProvider()

    token = set_services(StepServices(tool_registry=tool_reg, turn_registry=TurnRegistry()))
    try:
        t0 = time.monotonic()
        out = await asyncio.wait_for(
            _run_with_tools(_state("trace-deadline"), provider, tool_reg),  # type: ignore[arg-type]
            timeout=5.0,
        )
        elapsed = time.monotonic() - t0
    finally:
        reset_services(token)

    assert tool.cancelled, "the tool's OWN awaitable must be cancelled at the deadline"
    assert elapsed < 3.0, f"turn must finish within the bound, took {elapsed:.2f}s"
    # The loop continued past the bounded tool and produced an answer.
    assert out.responses
    assert any("final answer" in c.content for c in out.responses)


@pytest.mark.asyncio
async def test_stop_short_circuits_not_yet_started_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    """When a stop is ALREADY requested, the next tool call is short-circuited
    (the tool is never started) so the stop is honored at the boundary with
    near-zero added latency — no waiting on a fresh tool."""
    monkeypatch.setattr(execute_mod, "_TOOL_DEADLINE_S", 30.0)  # would block if started

    started = {"n": 0}

    class _CountingHang(_HangingTool):
        async def execute(self, **kwargs: object) -> ToolResult:
            started["n"] += 1
            return await super().execute(**kwargs)

    tool = _CountingHang()
    tool_reg = ToolRegistry()
    tool_reg.register(tool)

    reg = TurnRegistry()
    bg = asyncio.create_task(asyncio.sleep(0))
    request_id = "trace-stop-precheck"
    await reg.register(request_id, session_id="s1", task=bg, target=None, original_input="x")
    reg.request_stop(request_id)  # stop already pending when the tool is dispatched

    provider = _OneToolThenDoneProvider()
    token = set_services(StepServices(tool_registry=tool_reg, turn_registry=reg))
    try:
        out = await asyncio.wait_for(
            _run_with_tools(_state(request_id), provider, tool_reg),  # type: ignore[arg-type]
            timeout=5.0,
        )
    finally:
        reset_services(token)

    assert started["n"] == 0, "a tool must not be started once a stop is already pending"
    assert out.responses  # the turn still finalizes (a stopped/short-circuit answer)
    await bg
