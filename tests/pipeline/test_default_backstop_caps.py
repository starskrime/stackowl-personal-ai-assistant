"""BT Task 3 — default per-turn backstop: constants present + governor always wired.

Two tests:

1. test_backstop_constants_present_and_sane
   Verifies that DEFAULT_TURN_MAX_TIME_S and DEFAULT_TURN_MAX_STEPS exist in
   stackowl.authz.bounds and have the specified safe-backstop values, and that
   ResourceCaps() still defaults all-None (the backstop is injected only by
   execute.py when the owl sets no explicit caps, not in the model itself).

2. test_default_backstop_stops_loop_before_hard_cap
   Drives _run_with_tools with NO explicit caps on the owl (bounds=None), using
   a scripted provider that would loop past DEFAULT_TURN_MAX_STEPS (21 iterations).
   Asserts the governor stops it at or before DEFAULT_TURN_MAX_STEPS iterations —
   proving the backstop is ACTIVE and the loop does NOT reach the 30-iteration
   hard cap on a no-caps owl.
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.authz.bounds import DEFAULT_TURN_MAX_STEPS, DEFAULT_TURN_MAX_TIME_S, ResourceCaps
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps.execute import _run_with_tools
from stackowl.providers.react_callback import ReActIterationState
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.registry import ToolRegistry

# ---------------------------------------------------------------------------
# Test 1 — constants and ResourceCaps default
# ---------------------------------------------------------------------------


def test_backstop_constants_present_and_sane() -> None:
    """The two backstop constants must be importable and have the documented values."""
    assert DEFAULT_TURN_MAX_TIME_S == 120.0
    assert DEFAULT_TURN_MAX_STEPS == 20

    # ResourceCaps() must still default all-None — the backstop lives in execute.py,
    # not in the model.
    c = ResourceCaps()
    assert c.max_time_s is None and c.max_steps is None and c.max_cost_usd is None


# ---------------------------------------------------------------------------
# Shared scripted-provider harness (mirrors test_execute_budget.py)
# ---------------------------------------------------------------------------


class _LoopProvider:
    """Multi-iteration provider that propagates on_iteration_complete raises."""

    protocol = "anthropic"

    def __init__(self, iterations: int) -> None:
        self._iterations = iterations
        self.completed_iterations: list[int] = []

    async def complete_with_tools(
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
                # PROPAGATE — never swallow raises from the callback.
                await on_iteration_complete(
                    ReActIterationState(
                        iteration=i,
                        messages=list(all_messages),
                        tool_call_records=list(all_calls),
                    )
                )
            self.completed_iterations.append(i)
        return (f"done after {self._iterations} iterations", all_calls)


class _MinimalTool(Tool):
    """A read-severity tool — present so execute takes the tool-loop path."""

    @property
    def name(self) -> str:
        return "probe_tool"

    @property
    def description(self) -> str:
        return "Minimal read probe for the backstop test."

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {}}

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name="probe_tool",
            description=self.description,
            parameters=self.parameters,
            action_severity="read",
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        return ToolResult(success=True, output="probe-ok", duration_ms=1.0)


# ---------------------------------------------------------------------------
# Test 2 — behavioral: backstop stops a no-caps owl before the hard cap
# ---------------------------------------------------------------------------

# Drive more iterations than DEFAULT_TURN_MAX_STEPS to verify the backstop fires.
_SCRIPTED_ITERATIONS = DEFAULT_TURN_MAX_STEPS + 1  # 21


@pytest.mark.asyncio
async def test_default_backstop_stops_loop_before_hard_cap() -> None:
    """A no-caps owl must be stopped by the default backstop, not the 30-iter hard cap.

    Wires an owl with bounds=None (no explicit caps), and a scripted provider that
    would run DEFAULT_TURN_MAX_STEPS + 1 = 21 iterations. The default backstop
    (max_steps=DEFAULT_TURN_MAX_STEPS=20) must fire so that fewer than 21 iterations
    complete — specifically, completed_iterations must have <= DEFAULT_TURN_MAX_STEPS
    entries, and state.errors must carry a 'budget' marker.
    """
    tool = _MinimalTool()
    tool_registry = ToolRegistry()
    tool_registry.register(tool)

    # Owl with NO bounds at all — triggers the default backstop path.
    owl_registry = OwlRegistry()
    owl_registry.register(OwlAgentManifest(
        name="nocaps_owl",
        role="tester",
        system_prompt="Test default backstop.",
        model_tier="fast",
        bounds=None,  # NO explicit caps → default backstop must activate
    ))

    provider = _LoopProvider(iterations=_SCRIPTED_ITERATIONS)

    state = PipelineState(
        trace_id="trace-backstop-test",
        session_id="sess-backstop-test",
        input_text="run many steps",
        channel="cli",
        owl_name="nocaps_owl",
        pipeline_step="execute",
        interactive=False,  # non-interactive: no Raise prompt
    )

    token = set_services(StepServices(
        tool_registry=tool_registry,
        owl_registry=owl_registry,
        cost_tracker=None,
        clarify_gateway=None,
    ))  # type: ignore[arg-type]
    try:
        result = await _run_with_tools(state, provider, tool_registry)  # type: ignore[arg-type]
    finally:
        reset_services(token)

    # OUTCOME 1 — a budget marker must appear in errors (governor stopped the loop).
    assert any("budget" in e for e in result.errors), (
        f"DEFAULT BACKSTOP MISS: no 'budget' marker in errors. "
        f"The default backstop governor did not activate. errors={result.errors}"
    )

    # OUTCOME 2 — completed iterations must be <= DEFAULT_TURN_MAX_STEPS
    # (the governor fires at the boundary, so the scripted provider could not
    # finish all _SCRIPTED_ITERATIONS iterations).
    assert len(provider.completed_iterations) <= DEFAULT_TURN_MAX_STEPS, (
        f"DEFAULT BACKSTOP MISS: {len(provider.completed_iterations)} iterations completed, "
        f"expected <= {DEFAULT_TURN_MAX_STEPS}. The backstop did not fire in time. "
        f"completed_iterations={provider.completed_iterations}"
    )

    # OUTCOME 3 — a partial response chunk must be delivered (not an empty reply).
    assert result.responses, (
        f"Expected at least one ResponseChunk in state.responses after a backstop stop. "
        f"errors={result.errors}"
    )
