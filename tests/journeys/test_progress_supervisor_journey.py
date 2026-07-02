"""GATEWAY JOURNEY — Turn Progress Supervisor (Task 5).

Covers G1 (timeout spiral), G2 (refusal/no-op spiral), diverse-tool success,
transient-failure recovery, and the missing-param carry-forward from Task 3.
The AI provider is the ONLY mock; the real _run_with_tools/_dispatch path runs.

Falsification guards:
- diverse success over 6 different tools is NEVER tripped
- transient fail-fail-success recovers and delivers
- the consequential tally is NOT incremented by committed=False refusals (G2 honesty invariant)
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from stackowl.authz.bounds import BoundsSpec, ResourceCaps
from stackowl.config.test_mode import TestModeGuard
from stackowl.infra import recovery_context, tool_outcome_ledger
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.delivery_gate import surface_consequential_giveup_floor
from stackowl.pipeline.progress_tracker import NO_PROGRESS_THRESHOLD
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps.execute import _run_with_tools
from stackowl.pipeline.streaming import StreamRegistry
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

_OWL = "tps_owl"
_REFUSAL_MARK = "no longer available"


@pytest.fixture(autouse=True)
def _live_io():  # noqa: ANN202
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]
    yield
    TestModeGuard._active = prev  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Reusable tool classes
# ---------------------------------------------------------------------------


class _SimpleTool(Tool):
    """Single-outcome tool (always succeeds or always fails, configurable)."""

    def __init__(
        self,
        name: str,
        *,
        success: bool = True,
        side_effect_committed: bool = True,
        parameters: dict[str, object] | None = None,
        severity: str = "write",
    ) -> None:
        self._name = name
        self._success = success
        self._committed = side_effect_committed
        self._parameters = parameters or {"type": "object", "properties": {"x": {"type": "string"}}}
        self._severity = severity
        self.calls = 0

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"tool {self._name}"

    @property
    def parameters(self) -> dict[str, object]:
        return self._parameters

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self._name, description=self.description,
            parameters=self.parameters, action_severity=self._severity,
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        self.calls += 1
        if self._success:
            return ToolResult(success=True, output="ok", duration_ms=1.0)
        return ToolResult(
            success=False,
            side_effect_committed=self._committed,
            output="",
            error="declined",
            duration_ms=1.0,
        )


class _SlowTool(Tool):
    """Tool that sleeps longer than the configured deadline (G1 timeout shape)."""

    def __init__(self, name: str, sleep_s: float = 0.2) -> None:
        self._name = name
        self._sleep_s = sleep_s
        self.calls = 0

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"slow tool {self._name}"

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {"x": {"type": "string"}}}

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self._name, description=self.description,
            parameters=self.parameters, action_severity="write",
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        self.calls += 1  # increment BEFORE sleep so cancelled calls are counted
        await asyncio.sleep(self._sleep_s)
        return ToolResult(success=True, output="ok", duration_ms=1.0)


class _ScriptedTool(Tool):
    """Per-call scripted outcomes (for transient-fail-then-success patterns)."""

    def __init__(self, name: str, results: list[bool]) -> None:
        self._name = name
        self._results = results
        self.calls = 0

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"scripted tool {self._name}"

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {"x": {"type": "string"}}}

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self._name, description=self.description,
            parameters=self.parameters, action_severity="write",
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        idx = self.calls
        self.calls += 1
        ok = self._results[idx] if idx < len(self._results) else self._results[-1]
        if ok:
            return ToolResult(success=True, output="ok", duration_ms=1.0)
        return ToolResult(success=False, output="", error="boom", duration_ms=1.0)


# ---------------------------------------------------------------------------
# Provider stubs
# ---------------------------------------------------------------------------


class _SpiralProvider:
    """Keep dispatching `tool_name` until a dispatch returns the circuit-open refusal."""

    protocol = "anthropic"

    def __init__(self, tool_name: str, partial: str, max_attempts: int = 10) -> None:
        self._tool = tool_name
        self._partial = partial
        self._max = max_attempts

    async def complete_with_tools(  # noqa: ANN001
        self, *, user_text, system_text, tool_schemas, tool_dispatcher,
        history=None, on_iteration_complete=None, **_kwargs,
    ):
        records: list[dict[str, Any]] = []
        for i in range(self._max):
            out = await tool_dispatcher(self._tool, {"x": str(i)})
            records.append({"name": self._tool, "args": {"x": str(i)}, "result": out})
            if _REFUSAL_MARK in out:
                break
        return (self._partial, records)

    async def complete(self, messages, model, **kwargs):  # noqa: ANN201
        from stackowl.providers.base import CompletionResult
        return CompletionResult(
            content="x", input_tokens=1, output_tokens=1,
            model="m", provider_name=_OWL, duration_ms=1.0,
        )

    async def stream(self, *a, **k):  # pragma: no cover
        if False:  # noqa: SIM210
            yield ""


class _MissingParamSpiralProvider:
    """Like _SpiralProvider but always passes {} (no required param) to expose the
    pre-exec missing-param path — the tool body should never be called."""

    protocol = "anthropic"

    def __init__(self, tool_name: str, partial: str, max_attempts: int = 10) -> None:
        self._tool = tool_name
        self._partial = partial
        self._max = max_attempts

    async def complete_with_tools(  # noqa: ANN001
        self, *, user_text, system_text, tool_schemas, tool_dispatcher,
        history=None, on_iteration_complete=None, **_kwargs,
    ):
        records: list[dict[str, Any]] = []
        for _i in range(self._max):
            out = await tool_dispatcher(self._tool, {})  # missing required param "x"
            records.append({"name": self._tool, "args": {}, "result": out})
            if _REFUSAL_MARK in out:
                break
        return (self._partial, records)

    async def complete(self, messages, model, **kwargs):  # noqa: ANN201
        from stackowl.providers.base import CompletionResult
        return CompletionResult(
            content="x", input_tokens=1, output_tokens=1,
            model="m", provider_name=_OWL, duration_ms=1.0,
        )

    async def stream(self, *a, **k):  # pragma: no cover
        if False:  # noqa: SIM210
            yield ""


class _SeqProvider:
    """Dispatch a fixed (name, args) sequence; record rendered results."""

    protocol = "anthropic"

    def __init__(self, calls: list[tuple[str, dict[str, object]]], partial: str) -> None:
        self._calls = calls
        self._partial = partial
        self.rendered: list[str] = []

    async def complete_with_tools(  # noqa: ANN001
        self, *, user_text, system_text, tool_schemas, tool_dispatcher,
        history=None, on_iteration_complete=None, **_kwargs,
    ):
        records: list[dict[str, Any]] = []
        for name, args in self._calls:
            out = await tool_dispatcher(name, args)
            self.rendered.append(out)
            records.append({"name": name, "args": args, "result": out})
        return (self._partial, records)

    async def complete(self, messages, model, **kwargs):  # noqa: ANN201
        from stackowl.providers.base import CompletionResult
        return CompletionResult(
            content="x", input_tokens=1, output_tokens=1,
            model="m", provider_name=_OWL, duration_ms=1.0,
        )

    async def stream(self, *a, **k):  # pragma: no cover
        if False:  # noqa: SIM210
            yield ""


class _Reg:
    def __init__(self, p): self._p = p  # noqa: E704
    def get(self, name): return self._p  # noqa: E704
    def get_by_tier(self, tier): return self._p  # noqa: E704
    def get_with_cascade(self, t): return self._p  # noqa: E704


# ---------------------------------------------------------------------------
# Drive helper
# ---------------------------------------------------------------------------


async def _drive(tools: list[Tool], provider: Any) -> PipelineState:
    registry = ToolRegistry()
    for t in tools:
        registry.register(t)
    owl_registry = OwlRegistry()
    owl_registry.register(OwlAgentManifest(
        name=_OWL, role="t", system_prompt="t", model_tier="fast",
        bounds=BoundsSpec(
            tools=frozenset(t.name for t in tools),
            caps=ResourceCaps(max_steps=50),
        ),
    ))
    state = PipelineState(
        trace_id="t", session_id="s", input_text="make me a chart",
        channel="telegram", owl_name=_OWL, pipeline_step="execute", interactive=False,
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
        out = await _run_with_tools(state, provider, registry)  # type: ignore[arg-type]
        return await surface_consequential_giveup_floor(out)
    finally:
        recovery_context.reset(recovery_token)
        tool_outcome_ledger.reset(ledger_token)
        reset_services(token)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_slow_diverse_success_not_tripped() -> None:
    """6 different tools each called once and all succeeding — circuit never trips."""
    tool_names = ["tool_a", "tool_b", "tool_c", "tool_d", "tool_e", "tool_f"]
    tools = [_SimpleTool(n, success=True) for n in tool_names]
    calls = [(n, {"x": "1"}) for n in tool_names]
    provider = _SeqProvider(calls, partial="All six tasks are complete.")
    out = await _drive(tools, provider)

    delivered = "".join(c.content for c in out.responses)
    assert not any(getattr(c, "is_floor", False) for c in out.responses), (
        f"diverse-success turn was incorrectly floored. delivered={delivered!r}"
    )
    assert out.turn_made_progress is True, "diverse success must mark turn_made_progress=True"
    assert out.no_progress_tools == (), f"no tools should be bounced; got {out.no_progress_tools}"
    assert "All six tasks are complete." in delivered, (
        f"partial not delivered. delivered={delivered!r}"
    )


async def test_same_tool_failure_spiral_contained_and_floors() -> None:
    """A tool that always fails is bounced at exactly THRESHOLD; overclaim does NOT ship."""
    tool = _SimpleTool("bad_tool", success=False, side_effect_committed=True)
    provider = _SpiralProvider("bad_tool", "OVERCLAIM: all done perfectly!", max_attempts=10)
    out = await _drive([tool], provider)

    assert tool.calls == NO_PROGRESS_THRESHOLD, (
        f"expected exactly {NO_PROGRESS_THRESHOLD} executions before bounce, got {tool.calls}"
    )
    delivered = "".join(c.content for c in out.responses)
    assert "OVERCLAIM" not in delivered, f"OVERCLAIM shipped: {delivered!r}"
    assert any(getattr(c, "is_floor", False) for c in out.responses), (
        f"expected an honest is_floor chunk; got {out.responses!r}"
    )
    assert delivered.strip(), "floor must be non-empty"


async def test_timeout_spiral_contained_and_floors(monkeypatch: pytest.MonkeyPatch) -> None:
    """G1: a tool that always times out is bounced at THRESHOLD; overclaim does NOT ship."""
    monkeypatch.setattr("stackowl.pipeline.steps.execute._TOOL_DEADLINE_S", 0.05)
    tool = _SlowTool("slow_tool", sleep_s=0.2)
    provider = _SpiralProvider("slow_tool", "OVERCLAIM: all done!", max_attempts=10)
    out = await _drive([tool], provider)

    assert tool.calls == NO_PROGRESS_THRESHOLD, (
        f"expected exactly {NO_PROGRESS_THRESHOLD} timeout executions before bounce, got {tool.calls}"
    )
    delivered = "".join(c.content for c in out.responses)
    assert "OVERCLAIM" not in delivered, f"OVERCLAIM shipped after timeout spiral: {delivered!r}"
    assert any(getattr(c, "is_floor", False) for c in out.responses), (
        f"expected an honest is_floor chunk after timeout spiral; got {out.responses!r}"
    )


async def test_refusal_spiral_contained_and_floors() -> None:
    """G2: committed=False failures spiral is bounced at THRESHOLD; floor is honest;
    consequential tally stays at zero (the honesty invariant: no side_effect_committed=False
    refusal may trip the consequential give-up floor)."""
    tool = _SimpleTool("noop_tool", success=False, side_effect_committed=False)
    provider = _SpiralProvider("noop_tool", "OVERCLAIM: all done!", max_attempts=10)

    # Inline drive so we can read the consequential tally BEFORE the finally resets it.
    registry = ToolRegistry()
    registry.register(tool)
    owl_registry = OwlRegistry()
    owl_registry.register(OwlAgentManifest(
        name=_OWL, role="t", system_prompt="t", model_tier="fast",
        bounds=BoundsSpec(tools=frozenset({tool.name}), caps=ResourceCaps(max_steps=50)),
    ))
    state = PipelineState(
        trace_id="t", session_id="s", input_text="make me a chart",
        channel="telegram", owl_name=_OWL, pipeline_step="execute", interactive=False,
    )
    svc_token = set_services(StepServices(
        provider_registry=_Reg(provider),  # type: ignore[arg-type]
        tool_registry=registry, owl_registry=owl_registry,
        consent_gate=ConsequentialActionGate(confirm_fn=lambda _n: True),
        stream_registry=StreamRegistry(), cost_tracker=None,
    ))
    ledger_token = tool_outcome_ledger.bind()
    recovery_token = recovery_context.bind()
    try:
        out_raw = await _run_with_tools(state, provider, registry)  # type: ignore[arg-type]
        out = await surface_consequential_giveup_floor(out_raw)
        # Read tally while ledger ContextVar is still live
        cf, cs = tool_outcome_ledger.consequential_tally()
    finally:
        recovery_context.reset(recovery_token)
        tool_outcome_ledger.reset(ledger_token)
        reset_services(svc_token)

    assert tool.calls == NO_PROGRESS_THRESHOLD, (
        f"expected exactly {NO_PROGRESS_THRESHOLD} executions before bounce, got {tool.calls}"
    )
    delivered = "".join(c.content for c in out.responses)
    assert "OVERCLAIM" not in delivered, f"OVERCLAIM shipped after refusal spiral: {delivered!r}"
    assert any(getattr(c, "is_floor", False) for c in out.responses), (
        f"expected an honest is_floor chunk after refusal spiral; got {out.responses!r}"
    )
    # Honesty invariant: committed=False failures must NOT increment the consequential tally
    assert cf == 0, (
        f"committed=False refusals must not register as consequential failures; got cf={cf}"
    )


async def test_transient_failure_then_success_delivered() -> None:
    """fail-fail-success: never bounced, success delivered, no floor."""
    tool = _ScriptedTool("shell", results=[False, False, True])
    provider = _SeqProvider(
        [("shell", {"x": "1"}), ("shell", {"x": "2"}), ("shell", {"x": "3"})],
        partial="Shell done.",
    )
    out = await _drive([tool], provider)

    assert tool.calls == 3, f"fail-fail-success must run all three; got {tool.calls}"
    delivered = "".join(c.content for c in out.responses)
    assert "Shell done." in delivered, (
        f"a fail-fail-success turn must deliver its partial, not a floor. delivered={delivered!r}"
    )
    assert not any(getattr(c, "is_floor", False) for c in out.responses), (
        f"transient failure then success should NOT floor. delivered={delivered!r}"
    )


async def test_missing_param_refusal_spiral_contained() -> None:
    """Carry-forward (Task 3 G2): a model that always omits required params is contained.
    The tool body is NEVER called (pre-exec refusal fires); after NO_PROGRESS_THRESHOLD
    pre-exec bounces the tool is opened and the next call returns _circuit_open_refusal
    (contains 'no longer available'), breaking the spiral. The turn floors honestly.
    """
    tool = _SimpleTool(
        "param_tool",
        success=True,
        parameters={
            "type": "object",
            "properties": {"x": {"type": "string"}},
            "required": ["x"],
        },
    )
    provider = _MissingParamSpiralProvider("param_tool", "OVERCLAIM: done!", max_attempts=10)
    out = await _drive([tool], provider)

    # The tool body was never executed (pre-exec refusal fired for missing "x")
    assert tool.calls == 0, (
        f"tool body must never be called when required param is missing; got {tool.calls} calls"
    )
    delivered = "".join(c.content for c in out.responses)
    assert "OVERCLAIM" not in delivered, f"OVERCLAIM shipped after missing-param spiral: {delivered!r}"
    assert any(getattr(c, "is_floor", False) for c in out.responses), (
        f"expected an honest is_floor chunk after missing-param spiral; got {out.responses!r}"
    )
