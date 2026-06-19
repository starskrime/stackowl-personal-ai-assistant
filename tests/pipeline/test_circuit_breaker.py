"""Unit tests for the same-tool repeated-failure circuit breaker (incident P2)."""

from __future__ import annotations

from stackowl.pipeline.steps.execute import (
    SAME_TOOL_FAILURE_THRESHOLD,
    _circuit_open_refusal,
)


def test_threshold_is_three() -> None:
    # Host-agnostic fixed N; one below LoopGuard's identical-args break_at=4.
    assert SAME_TOOL_FAILURE_THRESHOLD == 3


def test_circuit_open_refusal_mentions_tool_and_steers_to_stop() -> None:
    msg = _circuit_open_refusal("shell")
    assert "shell" in msg
    # Steers the model to change approach or stop — no case-specifics.
    lower = msg.lower()
    assert "different" in lower or "another" in lower or "stop" in lower


def test_circuit_open_refusal_is_not_a_tool_failure_marker() -> None:
    # A bounce is containment, not a tool failure: it must NOT carry the marker
    # the give-up judge counts as a failed action (mirrors denied_this_run).
    from stackowl.pipeline.steps.execute import TOOL_FAILED_MARKER

    assert TOOL_FAILED_MARKER not in _circuit_open_refusal("shell")


import pytest
from typing import Any

from stackowl.authz.bounds import BoundsSpec, ResourceCaps
from stackowl.config.test_mode import TestModeGuard
from stackowl.infra import recovery_context, tool_outcome_ledger
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps.execute import _run_with_tools
from stackowl.pipeline.streaming import StreamRegistry
from stackowl.providers.react_callback import ReActIterationState
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

_OWL = "breaker_owl"


@pytest.fixture(autouse=True)
def _live_io():  # noqa: ANN202
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]
    yield
    TestModeGuard._active = prev  # type: ignore[attr-defined]


class _CountingTool(Tool):
    """A tool whose success/failure per call is scripted; counts its executions."""

    def __init__(self, name: str, results: list[bool], severity: str = "write") -> None:
        self._name = name
        self._results = results
        self._severity = severity
        self.calls = 0

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"test tool {self._name}"

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {"x": {"type": "string"}}}

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self._name, description=self.description,
            parameters=self.parameters, action_severity=self._severity,
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        idx = self.calls
        self.calls += 1
        ok = self._results[idx] if idx < len(self._results) else self._results[-1]
        if ok:
            return ToolResult(success=True, output="ok", duration_ms=1.0)
        # Genuine execution failure: ran and failed, boundary crossed (default True).
        return ToolResult(success=False, output="", error="boom", duration_ms=1.0)


class _SeqProvider:
    """Dispatch a fixed sequence of (name, args) through the real _dispatch and
    record each rendered result for assertions."""

    protocol = "anthropic"

    def __init__(self, calls: list[tuple[str, dict[str, object]]]) -> None:
        self._calls = calls
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
        return ("done", records)

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


async def _run(tools: list[Tool], calls: list[tuple[str, dict[str, object]]]) -> _SeqProvider:
    registry = ToolRegistry()
    for t in tools:
        registry.register(t)
    owl_registry = OwlRegistry()
    owl_registry.register(OwlAgentManifest(
        name=_OWL, role="t", system_prompt="t", model_tier="fast",
        bounds=BoundsSpec(tools=frozenset(t.name for t in tools), caps=ResourceCaps(max_steps=50)),
    ))
    provider = _SeqProvider(calls)
    state = PipelineState(
        trace_id="t", session_id="s", input_text="x", channel="telegram",
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
        return provider
    finally:
        recovery_context.reset(recovery_token)
        tool_outcome_ledger.reset(ledger_token)
        reset_services(token)


async def test_trips_after_threshold_and_bounces_further_calls() -> None:
    # 3 failures, then a 4th attempt → bounced (tool NOT executed a 4th time).
    tool = _CountingTool("shell", results=[False, False, False, False])
    calls = [("shell", {"x": str(i)}) for i in range(4)]
    provider = await _run([tool], calls)
    # Only 3 real executions; the 4th was bounced at dispatch.
    assert tool.calls == 3, f"expected 3 executions, got {tool.calls}"
    # The 4th rendered result is the circuit-open refusal, not a tool result.
    assert "no longer available" in provider.rendered[3]
    assert "shell" in provider.rendered[3]


async def test_success_resets_streak() -> None:
    # fail, fail, success, fail → streak is 1 after the last → NOT open.
    tool = _CountingTool("shell", results=[False, False, True, False])
    calls = [("shell", {"x": str(i)}) for i in range(4)]
    provider = await _run([tool], calls)
    assert tool.calls == 4, "a success between failures must reset; all 4 run"
    assert "no longer available" not in provider.rendered[3]


async def test_breaker_scoped_to_tool() -> None:
    # shell fails 3x (opens), but a different tool 'http' still runs.
    shell = _CountingTool("shell", results=[False, False, False])
    http = _CountingTool("http", results=[True])
    calls = [("shell", {"x": "1"}), ("shell", {"x": "2"}), ("shell", {"x": "3"}),
             ("http", {"x": "1"})]
    provider = await _run([shell, http], calls)
    assert shell.calls == 3
    assert http.calls == 1, "failures of shell must not open the breaker for http"
    assert "no longer available" not in provider.rendered[3]


async def test_bounce_records_no_effectful_failure() -> None:
    # The bounce must NOT increment the consequential failure tally (P0 honesty).
    tool = _CountingTool("shell", results=[False, False, False, False])
    calls = [("shell", {"x": str(i)}) for i in range(4)]
    # We need to inspect the ledger AFTER the run but BEFORE reset — drive inline.
    registry = ToolRegistry()
    registry.register(tool)
    owl_registry = OwlRegistry()
    owl_registry.register(OwlAgentManifest(
        name=_OWL, role="t", system_prompt="t", model_tier="fast",
        bounds=BoundsSpec(tools=frozenset({"shell"}), caps=ResourceCaps(max_steps=50)),
    ))
    provider = _SeqProvider(calls)
    state = PipelineState(
        trace_id="t", session_id="s", input_text="x", channel="telegram",
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
        cons_f, cons_s = tool_outcome_ledger.consequential_tally()
        # 3 genuine write failures recorded; the bounce recorded NOTHING extra.
        assert cons_f == 3, f"expected exactly 3 recorded failures, got {cons_f}"
    finally:
        recovery_context.reset(recovery_token)
        tool_outcome_ledger.reset(ledger_token)
        reset_services(token)
