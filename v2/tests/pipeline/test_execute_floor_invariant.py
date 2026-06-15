"""W2.T10 — the LOAD-BEARING responses-only floor invariant in execute.py.

Two exit paths in ``_run_with_tools`` currently hand the user NOTHING:

  1. The bare ``except Exception`` — a provider blew up mid-loop. It recorded an
     error marker but produced no response chunk, so the user saw silence.
  2. The normal exit ``if final_text:`` — when the provider returns an EMPTY
     final string, ``chunks`` stayed empty and the user again saw nothing.

This test pins THE invariant: the floor only ever ADDS to ``responses``. On the
hard-exception path it MUST also keep the original error in ``errors`` — three
consumers (durable status map, A2A status, parliament) infer success from
error-absence, so clearing/skipping the error would flip an honest failure into
a FAKE success. So: a non-empty honest response AND the error still recorded.

Drives the real ``_run_with_tools`` via the same harness as
``tests/pipeline/steps/test_execute_budget.py``.
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps.execute import _run_with_tools
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.registry import ToolRegistry

# ---------------------------------------------------------------------------
# Minimal tool so execute takes the tool-loop path
# ---------------------------------------------------------------------------

class _NoopTool(Tool):
    @property
    def name(self) -> str:
        return "noop_tool"

    @property
    def description(self) -> str:
        return "A tool for floor-invariant tests."

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {}}

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name="noop_tool", description=self.description,
            parameters=self.parameters, action_severity="read",
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        return ToolResult(success=True, output="RAN", duration_ms=1.0)


# ---------------------------------------------------------------------------
# Scripted providers
# ---------------------------------------------------------------------------

class _RaisingProvider:
    """Provider whose tool loop blows up mid-flight (the hard-exception path)."""

    protocol = "anthropic"

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
        raise RuntimeError("provider exploded mid-loop")


class _EmptyFinalProvider:
    """Provider that returns an EMPTY final string (loop-exhaustion / empty-final)."""

    protocol = "anthropic"

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
        return ("", [])


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

def _manifest(name: str) -> OwlAgentManifest:
    return OwlAgentManifest(
        name=name, role="r", system_prompt="s", model_tier="fast", bounds=None,
    )


async def _drive(provider: Any) -> PipelineState:
    tool_registry = ToolRegistry()
    tool_registry.register(_NoopTool())

    owl_registry = OwlRegistry()
    owl_registry.register(_manifest("o"))

    state = PipelineState(
        trace_id="trace-floor",
        session_id="sess-floor",
        input_text="please finish my task",
        channel="telegram",
        owl_name="o",
        pipeline_step="execute",
        interactive=False,
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
        return await _run_with_tools(state, provider, tool_registry)  # type: ignore[arg-type]
    finally:
        reset_services(token)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_hard_exception_floors_response_but_keeps_errors() -> None:
    out_state = await _drive(_RaisingProvider())

    # INVARIANT 1: user gets a non-empty honest response.
    assert out_state.responses, "hard-exception path produced no response chunk"
    assert out_state.responses[-1].content, "floored response chunk was empty"

    # INVARIANT 2: the error is STILL recorded — durable status stays failed,
    # not faked to success.
    assert out_state.errors, "hard-exception path dropped the error marker"
    assert any("execute:" in e for e in out_state.errors), (
        f"expected the original execute error marker, got: {out_state.errors}"
    )


@pytest.mark.asyncio
async def test_empty_final_text_floors() -> None:
    out_state = await _drive(_EmptyFinalProvider())

    # An empty final_text must never yield zero chunks.
    assert out_state.responses, "empty final_text produced no response chunk"
    assert out_state.responses[-1].content, "floored chunk for empty final was empty"


class _DeadlineCapturingProvider:
    """Records the wrapup_deadline_s the execute step threads in (F027 wiring)."""

    protocol = "anthropic"

    def __init__(self) -> None:
        self.seen_deadline: float | None = "UNSET"  # type: ignore[assignment]

    async def complete_with_tools(  # noqa: ANN001
        self,
        *,
        user_text: str,
        system_text: str,
        tool_schemas: list[dict[str, object]],
        tool_dispatcher: Any,
        history: list[Any] | None = None,
        on_iteration_complete: Any = None,
        wrapup_deadline_s: float | None = None,
        **_kwargs: object,
    ) -> tuple[str, list[dict[str, Any]]]:
        self.seen_deadline = wrapup_deadline_s
        return ("ok answer", [])


@pytest.mark.asyncio
async def test_execute_threads_residual_deadline_into_provider() -> None:
    """F027 — the execute step (governor owner) computes a residual wall-clock
    budget from its BudgetGovernor and passes it as wrapup_deadline_s. With no
    explicit owl caps the default 120s backstop applies, so the provider must
    receive a positive, bounded float (NOT the 'UNSET' sentinel, NOT None)."""
    provider = _DeadlineCapturingProvider()
    await _drive(provider)
    assert provider.seen_deadline != "UNSET", (
        "execute did not pass wrapup_deadline_s at all — F027 wiring missing"
    )
    assert isinstance(provider.seen_deadline, float)
    assert 0.0 < provider.seen_deadline <= 120.0, (
        f"residual deadline not bounded by the default backstop: {provider.seen_deadline}"
    )
