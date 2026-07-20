"""FX-07 — pipeline/steps/execute.py's build_tool_schemas must actually pass
``hydrated=`` into to_provider_schema. Verified live (before this fix) that it
never did: the ToolPresentation "hydrated" tier existed end-to-end but the one
call site that mattered always defaulted it to None.
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.authz.bounds import BoundsSpec, ResourceCaps
from stackowl.config.test_mode import TestModeGuard
from stackowl.infra import hydrated_tools, recovery_context, tool_outcome_ledger
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps.execute import _run_with_tools
from stackowl.pipeline.streaming import StreamRegistry
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

pytestmark = pytest.mark.asyncio

_OWL = "hydrated_owl"


@pytest.fixture(autouse=True)
def _live_io():  # noqa: ANN202
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]
    yield
    TestModeGuard._active = prev  # type: ignore[attr-defined]


class _StubTool(Tool):
    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"test tool {self._name}"

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {}}

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(name=self._name, description=self.description, parameters=self.parameters)

    async def execute(self, **kwargs: object) -> ToolResult:
        return ToolResult(success=True, output="ok", duration_ms=1.0)


class _CapturingRegistry(ToolRegistry):
    """Records every to_provider_schema call's kwargs for assertion."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[dict[str, Any]] = []

    def to_provider_schema(self, protocol: str, **kwargs: Any) -> list[dict[str, object]]:
        self.calls.append(kwargs)
        return super().to_provider_schema(protocol, **kwargs)


class _NoToolCallProvider:
    """A provider that never calls a tool — just proves schemas were built."""

    protocol = "anthropic"

    async def complete_with_tools(self, **_kwargs: Any) -> tuple[str, list[Any]]:
        return "done", []

    async def complete(self, messages: Any, model: str, **kwargs: Any) -> Any:
        from stackowl.providers.base import CompletionResult

        return CompletionResult(
            content="x", input_tokens=1, output_tokens=1,
            model="m", provider_name=_OWL, duration_ms=1.0,
        )

    async def stream(self, *a: Any, **k: Any):  # pragma: no cover
        if False:  # noqa: SIM210
            yield ""


class _Reg:
    def __init__(self, p: Any) -> None:
        self._p = p

    def get(self, name: str) -> Any:
        return self._p

    def get_by_tier(self, tier: str) -> Any:
        return self._p

    def get_with_cascade(self, t: Any) -> Any:
        return self._p


async def test_build_tool_schemas_passes_hydrated_from_session_store() -> None:
    session_id = "hydrated-wiring-test-session"
    hydrated_tools.clear(session_id)
    hydrated_tools.record(session_id, ["shell"])

    registry = _CapturingRegistry()
    registry.register(_StubTool("shell"))
    owl_registry = OwlRegistry()
    owl_registry.register(OwlAgentManifest(
        name=_OWL, role="t", system_prompt="t", model_tier="fast",
        bounds=BoundsSpec(tools=frozenset({"shell"}), caps=ResourceCaps(max_steps=50)),
    ))
    provider = _NoToolCallProvider()
    state = PipelineState(
        trace_id="t", session_id=session_id, input_text="x", channel="telegram",
        owl_name=_OWL, pipeline_step="execute", interactive=False,
    )
    token = set_services(StepServices(
        provider_registry=_Reg(provider),  # type: ignore[arg-type]
        tool_registry=registry, owl_registry=owl_registry,
        consent_gate=ConsequentialActionGate(confirm_fn=lambda _n: True),
        stream_registry=StreamRegistry(), cost_tracker=None,
    ))
    ledger_token = tool_outcome_ledger.bind()
    recovery_token = recovery_context.bind()
    try:
        await _run_with_tools(state, provider, registry)  # type: ignore[arg-type]
    finally:
        recovery_context.reset(recovery_token)
        tool_outcome_ledger.reset(ledger_token)
        reset_services(token)
        hydrated_tools.clear(session_id)

    assert registry.calls, "to_provider_schema was never called"
    assert any("shell" in (call.get("hydrated") or set()) for call in registry.calls), (
        f"hydrated tools never reached to_provider_schema: {registry.calls}"
    )


async def test_build_tool_schemas_empty_hydrated_for_unknown_session() -> None:
    """No prior tool_search hits for this session -> hydrated is empty, not None-crash."""
    session_id = "hydrated-wiring-test-session-empty"
    hydrated_tools.clear(session_id)

    registry = _CapturingRegistry()
    registry.register(_StubTool("shell"))
    owl_registry = OwlRegistry()
    owl_registry.register(OwlAgentManifest(
        name=_OWL, role="t", system_prompt="t", model_tier="fast",
        bounds=BoundsSpec(tools=frozenset({"shell"}), caps=ResourceCaps(max_steps=50)),
    ))
    provider = _NoToolCallProvider()
    state = PipelineState(
        trace_id="t", session_id=session_id, input_text="x", channel="telegram",
        owl_name=_OWL, pipeline_step="execute", interactive=False,
    )
    token = set_services(StepServices(
        provider_registry=_Reg(provider),  # type: ignore[arg-type]
        tool_registry=registry, owl_registry=owl_registry,
        consent_gate=ConsequentialActionGate(confirm_fn=lambda _n: True),
        stream_registry=StreamRegistry(), cost_tracker=None,
    ))
    ledger_token = tool_outcome_ledger.bind()
    recovery_token = recovery_context.bind()
    try:
        await _run_with_tools(state, provider, registry)  # type: ignore[arg-type]
    finally:
        recovery_context.reset(recovery_token)
        tool_outcome_ledger.reset(ledger_token)
        reset_services(token)

    assert registry.calls
    assert all(not call.get("hydrated") for call in registry.calls)
