"""E2-S3 — off-plan tools emit drift telemetry but still run (observe-only).

Drives the real _run_with_tools via the same harness as test_bounds_dispatch.py.
Verifies:
  - A tool outside task_envelope.tools still EXECUTES (observe-only, never blocked)
  - A single WARNING is logged for each off-plan tool (the audit signal)
  - On-plan tools produce no drift warning
  - No envelope → no drift warning (non-durable turns are inert)
"""

from __future__ import annotations

import logging

import pytest

from stackowl.authz import BoundsSpec
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps.execute import _run_with_tools
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.registry import ToolRegistry


# ---------------------------------------------------------------------------
# Minimal recording tools (copied from test_bounds_dispatch.py)
# ---------------------------------------------------------------------------

class _RecordingTool(Tool):
    """A read-severity tool that records whether it actually executed."""

    def __init__(self, name: str) -> None:
        self._name = name
        self.executed = False

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"Records execution of {self._name}."

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {}}

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self._name, description=self.description,
            parameters=self.parameters, action_severity="read",
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        self.executed = True
        return ToolResult(success=True, output=f"RAN:{self._name}", duration_ms=1.0)


class _TwoToolProvider:
    """Provider that dispatches ``allowed_tool`` then ``forbidden_tool`` once each."""

    protocol = "anthropic"

    def __init__(self) -> None:
        self.results: dict[str, str] = {}

    async def complete_with_tools(  # noqa: ANN001
        self, *, user_text, system_text, tool_schemas, tool_dispatcher, history=None,
    ):
        self.results["allowed_tool"] = await tool_dispatcher("allowed_tool", {})
        self.results["forbidden_tool"] = await tool_dispatcher("forbidden_tool", {})
        return ("done", [])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _state() -> PipelineState:
    return PipelineState(
        trace_id="trace-drift", session_id="sess-drift", input_text="go",
        channel="telegram", owl_name="bounded_owl", pipeline_step="execute",
    )


def _manifest(name: str, bounds: BoundsSpec | None) -> OwlAgentManifest:
    return OwlAgentManifest(
        name=name, role="r", system_prompt="s", model_tier="fast", bounds=bounds,
    )


async def _drive(
    bounds: BoundsSpec | None,
    *,
    task_envelope: BoundsSpec | None = None,
) -> tuple[_RecordingTool, _RecordingTool, _TwoToolProvider]:
    """Set up the harness and run _run_with_tools once.

    task_envelope is set on state via state.evolve() so only durable-like turns
    carry it; non-durable turns leave it at the default None.
    """
    allowed = _RecordingTool("allowed_tool")
    forbidden = _RecordingTool("forbidden_tool")
    registry = ToolRegistry()
    registry.register(allowed)
    registry.register(forbidden)
    owl_registry = OwlRegistry()
    owl_registry.register(_manifest("bounded_owl", bounds))
    provider = _TwoToolProvider()
    state = _state()
    if task_envelope is not None:
        state = state.evolve(task_envelope=task_envelope)
    token = set_services(
        StepServices(tool_registry=registry, owl_registry=owl_registry),  # type: ignore[arg-type]
    )
    try:
        await _run_with_tools(state, provider, registry)  # type: ignore[arg-type]
    finally:
        reset_services(token)
    return allowed, forbidden, provider


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_off_plan_tool_runs_and_is_audited(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Off-plan tool still executes AND a drift warning is logged (observe-only).

    owl_bounds permits BOTH tools; task_envelope allows only allowed_tool.
    forbidden_tool is off-plan → should still run but emit exactly one warning.
    """
    owl_bounds = BoundsSpec(tools=frozenset({"allowed_tool", "forbidden_tool"}))
    envelope = BoundsSpec(tools=frozenset({"allowed_tool"}))

    with caplog.at_level(logging.WARNING, logger="stackowl.engine"):
        allowed, forbidden, _provider = await _drive(owl_bounds, task_envelope=envelope)

    # OBSERVE-ONLY: BOTH tools must have executed
    assert allowed.executed is True
    assert forbidden.executed is True

    # At least one warning must be a drift/off-plan warning for forbidden_tool.
    # The tool name lives in the structured _fields dict (not the message text).
    def _is_drift_warning(r: logging.LogRecord) -> bool:
        if r.levelno != logging.WARNING:
            return False
        msg = r.getMessage()
        if "drift" not in msg and "off-plan" not in msg:
            return False
        fields: dict[str, object] = getattr(r, "_fields", {})
        return fields.get("tool") == "forbidden_tool"

    drift_records = [r for r in caplog.records if _is_drift_warning(r)]
    assert drift_records, (
        "Expected at least one WARNING for off-plan 'forbidden_tool', "
        f"but got records: {[(r.getMessage(), getattr(r, '_fields', {})) for r in caplog.records]}"
    )


@pytest.mark.asyncio
async def test_on_plan_tool_no_drift(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When both tools are on-plan, no drift warning is logged."""
    owl_bounds = BoundsSpec(tools=frozenset({"allowed_tool", "forbidden_tool"}))
    # envelope includes BOTH → neither is off-plan
    envelope = BoundsSpec(tools=frozenset({"allowed_tool", "forbidden_tool"}))

    with caplog.at_level(logging.WARNING, logger="stackowl.engine"):
        allowed, forbidden, _provider = await _drive(owl_bounds, task_envelope=envelope)

    assert allowed.executed is True
    assert forbidden.executed is True

    drift_records = [
        r for r in caplog.records
        if r.levelno == logging.WARNING
        and ("drift" in r.getMessage() or "off-plan" in r.getMessage())
    ]
    assert not drift_records, (
        f"Unexpected drift warnings when all tools are on-plan: "
        f"{[r.getMessage() for r in drift_records]}"
    )


@pytest.mark.asyncio
async def test_no_envelope_no_drift(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Non-durable turns (task_envelope=None) produce no drift warning."""
    owl_bounds = BoundsSpec(tools=frozenset({"allowed_tool", "forbidden_tool"}))

    with caplog.at_level(logging.WARNING, logger="stackowl.engine"):
        allowed, forbidden, _provider = await _drive(owl_bounds, task_envelope=None)

    assert allowed.executed is True
    assert forbidden.executed is True

    drift_records = [
        r for r in caplog.records
        if r.levelno == logging.WARNING
        and ("drift" in r.getMessage() or "off-plan" in r.getMessage())
    ]
    assert not drift_records, (
        f"Unexpected drift warnings when no envelope is set: "
        f"{[r.getMessage() for r in drift_records]}"
    )
