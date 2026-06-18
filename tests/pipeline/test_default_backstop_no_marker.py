"""BT Task 4 — default backstop delivers a clean partial (no budget marker).

Two cases:

1. test_default_backstop_breach_no_budget_marker
   DEFAULT backstop (no explicit caps on owl) triggers a BudgetBreach.
   The scripted provider emits a non-empty partial text at the breach point.
   The delivered response text must NOT contain the 'budget:stop' / 'budget'
   marker substring — the user sees the clean best-available partial.
   The marker IS still in state.errors (internal record), NOT in the content.

2. test_default_backstop_breach_empty_partial_floors
   DEFAULT backstop triggers a BudgetBreach with empty partial_text (the
   provider has nothing at the breach point).  The delivered response must:
     * contain at least one non-empty ResponseChunk (never-empty invariant);
     * NOT contain the 'budget:stop' marker substring in any chunk's content.

3. test_explicit_cap_breach_has_budget_marker
   EXPLICIT cap (owl sets max_steps=2) triggers a BudgetBreach.
   The delivered response text MUST contain the budget note substring
   'budget cap' and/or 'stopped' (unchanged from the existing behaviour).
   The errors list MUST carry a 'budget:stop:...' marker.

Harness mirrors test_default_backstop_caps.py (_LoopProvider / _MinimalTool).
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
# Shared scripted-provider harness (mirrors test_default_backstop_caps.py)
# ---------------------------------------------------------------------------


class _LoopProvider:
    """Multi-iteration provider that propagates on_iteration_complete raises.

    Emits an assistant message at each iteration so exc.partial_text is
    non-empty at the breach point (by default).
    """

    protocol = "anthropic"

    def __init__(self, iterations: int, *, emit_partial: bool = True) -> None:
        self._iterations = iterations
        self._emit_partial = emit_partial
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
            if self._emit_partial:
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
        return "Minimal read probe for the no-marker test."

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
# Shared fixture helpers
# ---------------------------------------------------------------------------

_SCRIPTED_ITERATIONS_DEFAULT = 21  # one more than DEFAULT_TURN_MAX_STEPS (20)
_EXPLICIT_CAP_STEPS = 2


def _make_services(
    owl_name: str,
    *,
    explicit_caps: ResourceCaps | None = None,
) -> StepServices:
    """Wire StepServices for a no-caps or explicit-caps owl."""
    tool = _MinimalTool()
    tool_registry = ToolRegistry()
    tool_registry.register(tool)

    owl_registry = OwlRegistry()
    if explicit_caps is not None:
        bounds = BoundsSpec(
            tools=frozenset({"probe_tool"}),
            caps=explicit_caps,
        )
        owl_registry.register(OwlAgentManifest(
            name=owl_name,
            role="tester",
            system_prompt="Test caps.",
            model_tier="fast",
            bounds=bounds,
        ))
    else:
        # No explicit caps → default backstop activates in execute.py
        owl_registry.register(OwlAgentManifest(
            name=owl_name,
            role="tester",
            system_prompt="Test default backstop.",
            model_tier="fast",
            bounds=None,
        ))

    return StepServices(
        tool_registry=tool_registry,
        owl_registry=owl_registry,
        cost_tracker=None,
        clarify_gateway=None,
    )  # type: ignore[arg-type]


def _make_state(owl_name: str) -> PipelineState:
    return PipelineState(
        trace_id=f"trace-{owl_name}",
        session_id=f"sess-{owl_name}",
        input_text="run many steps",
        channel="cli",
        owl_name=owl_name,
        pipeline_step="execute",
        interactive=False,
    )


# ===========================================================================
# Test 1 — DEFAULT backstop breach: clean partial, NO budget marker in content
# ===========================================================================


@pytest.mark.asyncio
async def test_default_backstop_breach_no_budget_marker() -> None:
    """DEFAULT backstop (no explicit caps): delivered content must NOT contain 'budget:stop'.

    The scripted provider emits non-empty partial text at the breach point.
    The response chunk's content must be the clean partial text only —
    none of the 'budget:stop' / 'budget cap' / 'stopped' developer marker.
    The internal state.errors entry may still carry a budget marker (logged
    only internally), but the delivered chunk content must be clean.
    """
    owl_name = "nocaps_nomark"
    provider = _LoopProvider(iterations=_SCRIPTED_ITERATIONS_DEFAULT, emit_partial=True)
    services = _make_services(owl_name)
    state = _make_state(owl_name)

    token = set_services(services)
    try:
        result = await _run_with_tools(state, provider, ToolRegistry())  # type: ignore[arg-type]
    finally:
        reset_services(token)

    # The governor must have fired (loop was stopped before all iterations).
    assert len(provider.completed_iterations) < _SCRIPTED_ITERATIONS_DEFAULT, (
        "DEFAULT BACKSTOP DID NOT FIRE: all iterations completed. "
        f"completed_iterations={provider.completed_iterations}"
    )

    # At least one response chunk must be delivered (never-empty invariant).
    assert result.responses, (
        "No ResponseChunk delivered after default backstop breach — never-empty violated."
    )

    # LOAD-BEARING: no chunk content should contain the developer budget marker.
    all_content = "".join(c.content for c in result.responses)
    assert "budget:stop" not in all_content, (
        f"DEFAULT BACKSTOP FAIL: 'budget:stop' marker found in delivered content. "
        f"Content: {all_content!r}"
    )
    assert "budget cap" not in all_content.lower(), (
        f"DEFAULT BACKSTOP FAIL: 'budget cap' note found in delivered content. "
        f"Content: {all_content!r}"
    )
    assert "stopped: budget" not in all_content.lower(), (
        f"DEFAULT BACKSTOP FAIL: 'stopped: budget' note found in delivered content. "
        f"Content: {all_content!r}"
    )


# ===========================================================================
# Test 2 — DEFAULT backstop + empty partial: routes to floor, no marker
# ===========================================================================


@pytest.mark.asyncio
async def test_default_backstop_breach_empty_partial_floors() -> None:
    """DEFAULT backstop + empty partial_text: never-empty floor fires, no marker in content.

    When the provider has no assistant message at the breach point (emit_partial=False),
    exc.partial_text is ''. The handler must NOT emit an empty chunk or a
    raw budget marker — it must route to the synthesize_floor path.
    """
    owl_name = "nocaps_empty"
    provider = _LoopProvider(iterations=_SCRIPTED_ITERATIONS_DEFAULT, emit_partial=False)
    services = _make_services(owl_name)
    state = _make_state(owl_name)

    token = set_services(services)
    try:
        result = await _run_with_tools(state, provider, ToolRegistry())  # type: ignore[arg-type]
    finally:
        reset_services(token)

    # The governor must have fired.
    assert len(provider.completed_iterations) < _SCRIPTED_ITERATIONS_DEFAULT, (
        "DEFAULT BACKSTOP DID NOT FIRE with empty partial. "
        f"completed_iterations={provider.completed_iterations}"
    )

    # Must still produce at least one non-empty response chunk (floor invariant).
    assert result.responses, (
        "No ResponseChunk delivered after default backstop + empty partial — floor did not fire."
    )
    all_content = "".join(c.content for c in result.responses)
    assert all_content.strip(), (
        "Empty content delivered after default backstop + empty partial — never-empty violated."
    )

    # LOAD-BEARING: no raw developer marker in content ('budget:stop:...' form).
    # The empty-partial default backstop now routes to the graceful slot-free
    # floor (intent-classification-hardening), so neither the 'budget:stop:'
    # marker nor the 'budget cap reached' exception text appears in content.
    assert "budget:stop" not in all_content, (
        f"DEFAULT BACKSTOP EMPTY-PARTIAL FAIL: 'budget:stop' marker in delivered content. "
        f"Content: {all_content!r}"
    )


# ===========================================================================
# Test 3 — EXPLICIT cap breach: marker IS present (unchanged behaviour)
# ===========================================================================


@pytest.mark.asyncio
async def test_explicit_cap_breach_has_budget_marker() -> None:
    """EXPLICIT caps (max_steps=2): marker in errors + budget note in content (unchanged).

    When the owl has an explicit ResourceCaps, the budget marker ('budget:stop:...')
    MUST appear in state.errors AND the 'budget cap' / 'stopped' note MUST be
    present in the delivered response content — this is the existing behaviour that
    must NOT be regressed.
    """
    owl_name = "explicit_cap_owl"
    caps = ResourceCaps(max_steps=_EXPLICIT_CAP_STEPS)
    provider = _LoopProvider(iterations=5, emit_partial=True)
    services = _make_services(owl_name, explicit_caps=caps)
    state = _make_state(owl_name)

    token = set_services(services)
    try:
        result = await _run_with_tools(state, provider, ToolRegistry())  # type: ignore[arg-type]
    finally:
        reset_services(token)

    # The governor must have stopped the loop.
    assert len(provider.completed_iterations) < 5, (
        "EXPLICIT CAP DID NOT FIRE: all 5 iterations completed. "
        f"completed_iterations={provider.completed_iterations}"
    )

    # LOAD-BEARING: 'budget:stop:...' marker in errors (unchanged behaviour).
    assert any("budget:stop" in e for e in result.errors), (
        f"EXPLICIT CAP REGRESSION: 'budget:stop' marker missing from errors. "
        f"errors={result.errors}"
    )

    # LOAD-BEARING: budget note in delivered content (unchanged behaviour).
    all_content = "".join(c.content for c in result.responses)
    assert "budget cap" in all_content.lower() or "stopped" in all_content.lower(), (
        f"EXPLICIT CAP REGRESSION: budget-stop note missing from delivered content. "
        f"Content: {all_content!r}"
    )
