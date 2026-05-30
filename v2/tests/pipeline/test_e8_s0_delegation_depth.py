"""E8-S0 — delegation_depth on PipelineState + child-toolset exclusion."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Literal

import pytest

from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps.execute import (
    _CHILD_EXCLUDED_TOOLS,
    _exclude_spawn_tools,
    _run_with_tools,
    _schema_tool_name,
)
from stackowl.tools.base import Tool, ToolResult
from stackowl.tools.registry import ToolRegistry


def _state(**kwargs: object) -> PipelineState:
    base: dict[str, object] = {
        "trace_id": "t",
        "session_id": "s",
        "input_text": "hi",
        "channel": "cli",
        "owl_name": "secretary",
        "pipeline_step": "start",
    }
    base.update(kwargs)
    return PipelineState(**base)  # type: ignore[arg-type]


class TestDelegationDepthField:
    def test_defaults_to_zero(self) -> None:
        assert _state().delegation_depth == 0

    def test_increments_per_level(self) -> None:
        parent = _state()
        child = parent.evolve(delegation_depth=parent.delegation_depth + 1)
        grandchild = child.evolve(delegation_depth=child.delegation_depth + 1)
        assert parent.delegation_depth == 0
        assert child.delegation_depth == 1
        assert grandchild.delegation_depth == 2

    def test_preserved_across_unrelated_evolve(self) -> None:
        """An evolve() that does not mention delegation_depth must preserve it."""
        s = _state(delegation_depth=2)
        evolved = s.evolve(input_text="new task", owl_name="research")
        assert evolved.delegation_depth == 2  # carried through model_copy


class TestChildToolsetExclusion:
    def test_excluded_set_contains_the_two_spawn_tools(self) -> None:
        assert "delegate_task" in _CHILD_EXCLUDED_TOOLS
        assert "sessions_spawn" in _CHILD_EXCLUDED_TOOLS

    def test_schema_name_extraction_anthropic_shape(self) -> None:
        assert _schema_tool_name({"name": "delegate_task"}) == "delegate_task"

    def test_schema_name_extraction_openai_shape(self) -> None:
        schema = {"type": "function", "function": {"name": "sessions_spawn"}}
        assert _schema_tool_name(schema) == "sessions_spawn"

    def test_exclusion_removes_both_tools_anthropic(self) -> None:
        schemas: list[dict[str, object]] = [
            {"name": "read_file"},
            {"name": "delegate_task"},
            {"name": "sessions_spawn"},
            {"name": "shell"},
        ]
        result = _exclude_spawn_tools(schemas)
        names = {_schema_tool_name(s) for s in result}
        assert names == {"read_file", "shell"}

    def test_exclusion_removes_both_tools_openai(self) -> None:
        def fn(name: str) -> dict[str, object]:
            return {"type": "function", "function": {"name": name}}

        schemas = [fn("read_file"), fn("delegate_task"), fn("sessions_spawn")]
        result = _exclude_spawn_tools(schemas)
        names = {_schema_tool_name(s) for s in result}
        assert names == {"read_file"}

    def test_depth_zero_keeps_all_tools(self) -> None:
        """At depth 0 the exclusion is a no-op (the presented set is unchanged)."""
        schemas: list[dict[str, object]] = [
            {"name": "delegate_task"},
            {"name": "sessions_spawn"},
            {"name": "read_file"},
        ]
        # The execute step only calls _exclude_spawn_tools when depth>0; at depth
        # 0 the full list is presented untouched.
        assert _state(delegation_depth=0).delegation_depth == 0
        # Sanity: the helper itself always excludes when called.
        assert len(_exclude_spawn_tools(schemas)) == 1


class _StubTool(Tool):
    """Minimal read-only tool with a fixed name for presentation tests."""

    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"stub {self._name}"

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: object) -> ToolResult:
        return ToolResult(success=True, output="ok")


class _CapturingProvider:
    """Provider stub that records the tool_schemas it is presented."""

    def __init__(self) -> None:
        self.seen_schemas: list[dict[str, Any]] = []

    @property
    def protocol(self) -> Literal["openai", "anthropic", "gemini"]:
        return "anthropic"

    async def complete_with_tools(
        self,
        user_text: str,
        system_text: str | None,
        tool_schemas: list[dict[str, Any]],
        tool_dispatcher: Callable[[str, dict[str, Any]], Awaitable[str]],
        max_iterations: int = 8,
    ) -> tuple[str, list[dict[str, Any]]]:
        self.seen_schemas = tool_schemas
        return "done", []


def _registry_with_spawn_tools() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(_StubTool("read_file"))
    reg.register(_StubTool("delegate_task"))
    reg.register(_StubTool("sessions_spawn"))
    return reg


class TestExecuteStepDepthGating:
    """Integration: the real execute step gates on state.delegation_depth."""

    async def test_depth_gt_zero_excludes_spawn_tools_in_presented_set(self) -> None:
        reg = _registry_with_spawn_tools()
        provider = _CapturingProvider()
        token = set_services(StepServices())  # no owl_registry → full catalog
        try:
            await _run_with_tools(_state(delegation_depth=1), provider, reg)  # type: ignore[arg-type]
        finally:
            reset_services(token)
        names = {_schema_tool_name(s) for s in provider.seen_schemas}
        assert "delegate_task" not in names
        assert "sessions_spawn" not in names
        assert "read_file" in names

    async def test_depth_zero_includes_spawn_tools_in_presented_set(self) -> None:
        reg = _registry_with_spawn_tools()
        provider = _CapturingProvider()
        token = set_services(StepServices())
        try:
            await _run_with_tools(_state(delegation_depth=0), provider, reg)  # type: ignore[arg-type]
        finally:
            reset_services(token)
        names = {_schema_tool_name(s) for s in provider.seen_schemas}
        assert "delegate_task" in names
        assert "sessions_spawn" in names
        assert "read_file" in names


class _RecordingTool(Tool):
    """Tool that records whether its execute() actually ran."""

    def __init__(self, name: str) -> None:
        self._name = name
        self.ran = False

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"recording {self._name}"

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: object) -> ToolResult:
        self.ran = True
        return ToolResult(success=True, output="EXECUTED")


class _InvokingProvider:
    """Adversarial provider: calls an EXCLUDED tool BY NAME via the dispatcher,
    even though it was never presented (presentation != authorization)."""

    def __init__(self, tool_name: str) -> None:
        self._tool_name = tool_name
        self.dispatch_result: str | None = None

    @property
    def protocol(self) -> Literal["openai", "anthropic", "gemini"]:
        return "anthropic"

    async def complete_with_tools(
        self, user_text: str, system_text: str | None,
        tool_schemas: list[dict[str, Any]],
        tool_dispatcher: Callable[[str, dict[str, Any]], Awaitable[str]],
        max_iterations: int = 8,
    ) -> tuple[str, list[dict[str, Any]]]:
        self.dispatch_result = await tool_dispatcher(self._tool_name, {})
        return "done", []


class TestExecuteLayerDepthEnforcement:
    """EXECUTION-layer cap: a depth>0 child that NAMES an excluded tool is refused
    at dispatch and the tool never runs (presentation-only would be theater)."""

    async def test_depth_gt_zero_refuses_excluded_tool_at_dispatch(self) -> None:
        reg = ToolRegistry()
        target = _RecordingTool("delegate_task")
        reg.register(target)
        provider = _InvokingProvider("delegate_task")
        token = set_services(StepServices())
        try:
            await _run_with_tools(_state(delegation_depth=1), provider, reg)  # type: ignore[arg-type]
        finally:
            reset_services(token)
        assert target.ran is False  # the tool was NOT executed
        assert provider.dispatch_result is not None
        assert "not available to a delegated sub-agent" in provider.dispatch_result
        # (Depth-0 ALLOW is covered by the presentation test + the gate only
        # firing at depth>0; exercising the full depth-0 execution path needs the
        # consent-gate machinery, out of scope for this unit.)


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-q"])
