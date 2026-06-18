"""E2-S4 — budget governor is wired into the execute step iteration seam.

Drives the real _run_with_tools via the same harness as test_bounds_dispatch.py.
The scripted provider runs a multi-iteration loop, calling on_iteration_complete
each round.  With max_steps=2 the gate raises BudgetBreach at iteration=1
(steps_done=2) — the execute step must catch it and return a partial state with:
  * a budget marker in state.errors
  * at least one ResponseChunk in state.responses (partial delivery)
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.authz.bounds import BoundsSpec, ResourceCaps
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps.execute import _run_with_tools
from stackowl.providers.react_callback import ReActIterationState
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.registry import ToolRegistry


# ---------------------------------------------------------------------------
# Minimal tool (needed so execute takes the tool-loop path)
# ---------------------------------------------------------------------------

class _LoopTool(Tool):
    """A read-severity tool that records executions."""

    def __init__(self) -> None:
        self.executed = False

    @property
    def name(self) -> str:
        return "loop_tool"

    @property
    def description(self) -> str:
        return "A tool for budget iteration tests."

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {}}

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name="loop_tool", description=self.description,
            parameters=self.parameters, action_severity="read",
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        self.executed = True
        return ToolResult(success=True, output="RAN:loop_tool", duration_ms=1.0)


# ---------------------------------------------------------------------------
# Scripted multi-iteration provider
# ---------------------------------------------------------------------------

class _MultiIterationProvider:
    """Provider that runs N iterations, calling on_iteration_complete each round.

    Each iteration sends a synthetic assistant message then calls the callback.
    If the callback raises, the exception propagates (breaking the loop) —
    exactly what a real provider does.
    """

    protocol = "anthropic"

    def __init__(self, iterations: int = 5) -> None:
        self._iterations = iterations
        self.completed_iterations: list[int] = []

    async def complete_with_tools(  # noqa: ANN001
        self,
        *,
        user_text: str,
        system_text: str,
        tool_schemas: list[dict[str, object]],
        tool_dispatcher: Any,
        history: list[Any] | None = None,
        on_iteration_complete: Any = None,
        **_kwargs: object,
    ) -> tuple[str, list[dict[str, Any]]]:
        all_messages: list[dict[str, Any]] = []
        all_calls: list[dict[str, Any]] = []
        for i in range(self._iterations):
            all_messages.append({"role": "assistant", "content": f"step{i}"})
            if on_iteration_complete is not None:
                await on_iteration_complete(
                    ReActIterationState(
                        iteration=i,
                        messages=list(all_messages),
                        tool_call_records=list(all_calls),
                    )
                )
            self.completed_iterations.append(i)
        return (f"done after {self._iterations} iterations", all_calls)


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

def _manifest(name: str, bounds: BoundsSpec | None) -> OwlAgentManifest:
    return OwlAgentManifest(
        name=name, role="r", system_prompt="s", model_tier="fast", bounds=bounds,
    )


async def _drive_capped(
    owl_bounds: BoundsSpec,
    *,
    interactive: bool,
    iterations: int = 5,
) -> PipelineState:
    """Build harness, run _run_with_tools, return the resulting PipelineState."""
    tool = _LoopTool()
    tool_registry = ToolRegistry()
    tool_registry.register(tool)

    owl_registry = OwlRegistry()
    owl_registry.register(_manifest("o", owl_bounds))

    provider = _MultiIterationProvider(iterations=iterations)

    state = PipelineState(
        trace_id="trace-budget",
        session_id="sess-budget",
        input_text="run many steps",
        channel="telegram",
        owl_name="o",
        pipeline_step="execute",
        interactive=interactive,
    )

    token = set_services(
        StepServices(
            tool_registry=tool_registry,
            owl_registry=owl_registry,
            cost_tracker=None,
            clarify_gateway=None,
        ),  # type: ignore[arg-type]
    )
    try:
        result = await _run_with_tools(state, provider, tool_registry)  # type: ignore[arg-type]
    finally:
        reset_services(token)
    return result


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_non_interactive_step_cap_stops_with_partial() -> None:
    """Non-interactive run: max_steps=2 → BudgetBreach at iteration 1.

    execute must catch the breach, record a budget marker in errors, and deliver
    the partial text as a ResponseChunk in responses.
    """
    owl_bounds = BoundsSpec(tools=frozenset({"loop_tool"}), caps=ResourceCaps(max_steps=2))
    state = await _drive_capped(owl_bounds, interactive=False, iterations=5)

    # Budget marker must appear in errors
    assert any("budget" in e for e in state.errors), (
        f"Expected a 'budget' marker in state.errors, got: {state.errors}"
    )

    # Partial text must be delivered as a ResponseChunk
    assert state.responses, (
        f"Expected at least one ResponseChunk in state.responses, got none. "
        f"errors={state.errors}"
    )


@pytest.mark.asyncio
async def test_no_caps_runs_to_completion() -> None:
    """When caps are all-None the governor is a no-op — all iterations complete."""
    owl_bounds = BoundsSpec(tools=frozenset({"loop_tool"}))  # no caps
    state = await _drive_capped(owl_bounds, interactive=False, iterations=3)

    # No budget errors
    assert not any("budget" in e for e in state.errors), (
        f"Unexpected budget error with no caps: {state.errors}"
    )
    # The final response text must be present
    assert state.responses, "Expected a response chunk for a run that completed normally"


@pytest.mark.asyncio
async def test_unbounded_owl_runs_to_completion() -> None:
    """An owl with bounds=None is completely unrestricted — all iterations complete."""
    state = await _drive_capped(
        BoundsSpec(tools=frozenset({"loop_tool"}), caps=ResourceCaps()),
        interactive=False,
        iterations=3,
    )
    assert not any("budget" in e for e in state.errors), (
        f"Unexpected budget error for all-None caps: {state.errors}"
    )
    assert state.responses
