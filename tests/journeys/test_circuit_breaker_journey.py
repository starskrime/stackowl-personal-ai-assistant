"""GATEWAY JOURNEY — the same-tool circuit breaker contains a spiral (incident P2).

Reproduces the pictures-overclaim spiral shape: a tool fails repeatedly. After
SAME_TOOL_FAILURE_THRESHOLD consecutive failures the tool is BOUNCED at dispatch
(not executed again), the budget is not burned to the wall, and a turn that
delivered nothing real floors HONESTLY (no overclaim). The AI provider is the
ONLY mock; the real _run_with_tools/_dispatch path runs.

Falsification guards: a tool that fails twice then SUCCEEDS is never bounced and
its success is delivered; failures of tool X do not open the breaker for tool Y.
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.authz.bounds import BoundsSpec, ResourceCaps
from stackowl.config.test_mode import TestModeGuard
from stackowl.infra import recovery_context, tool_outcome_ledger
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.giveup_floor import surface_consequential_giveup_floor
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps.execute import (
    SAME_TOOL_FAILURE_THRESHOLD,
    _run_with_tools,
)
from stackowl.pipeline.streaming import StreamRegistry
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

_OWL = "breaker_owl"
_OVERCLAIM = "All set — your files are ready and will look great! 🎨"
_REFUSAL_MARK = "no longer available"


@pytest.fixture(autouse=True)
def _live_io():  # noqa: ANN202
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]
    yield
    TestModeGuard._active = prev  # type: ignore[attr-defined]


class _ScriptedFailTool(Tool):
    """A tool whose per-call outcome is scripted; counts executions."""

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
        return f"tool {self._name}"

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
        return ToolResult(success=False, output="", error="boom", duration_ms=1.0)


class _SpiralProvider:
    """Keep calling `spiral_tool` until a dispatch returns the circuit-open refusal,
    then emit a final partial (the would-be overclaim) and stop. Models a weak model
    that keeps retrying a broken tool until it is cut off."""

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
                break  # bounced — the model stops retrying
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


async def _drive(tools: list[Tool], provider: Any) -> PipelineState:
    registry = ToolRegistry()
    for t in tools:
        registry.register(t)
    owl_registry = OwlRegistry()
    owl_registry.register(OwlAgentManifest(
        name=_OWL, role="t", system_prompt="t", model_tier="fast",
        bounds=BoundsSpec(tools=frozenset(t.name for t in tools), caps=ResourceCaps(max_steps=50)),
    ))
    state = PipelineState(
        trace_id="t", session_id="s", input_text="can you help me with pictures",
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
# THE INCIDENT — a spiraling tool is contained, the turn floors honestly.
# ---------------------------------------------------------------------------


async def test_spiral_is_contained_and_turn_floors_honestly() -> None:
    tool = _ScriptedFailTool("shell", results=[False])  # always fails
    provider = _SpiralProvider("shell", _OVERCLAIM, max_attempts=10)
    out = await _drive([tool], provider)

    # CONTAINED: the tool executed exactly THRESHOLD times, then was bounced —
    # NOT run a (THRESHOLD+1)th time, and nowhere near the 10 attempts the model
    # would otherwise have made (the incident's 9 shells).
    assert tool.calls == SAME_TOOL_FAILURE_THRESHOLD, (
        f"expected exactly {SAME_TOOL_FAILURE_THRESHOLD} executions, got {tool.calls}"
    )

    delivered = "".join(c.content for c in out.responses)
    # HONEST: the would-be overclaim did NOT ship; an honest floor did.
    assert "look great" not in delivered, f"OVERCLAIM SHIPPED: {delivered!r}"
    assert any(getattr(c, "is_floor", False) for c in out.responses), (
        f"expected an honest is_floor chunk; got {out.responses!r}"
    )
    assert delivered.strip(), "floor must be non-empty"


# ---------------------------------------------------------------------------
# FALSIFICATION (a) — fail twice then succeed is NEVER bounced; success delivered.
# ---------------------------------------------------------------------------


async def test_transient_failure_then_success_is_not_bounced() -> None:
    tool = _ScriptedFailTool("shell", results=[False, False, True])
    provider = _SeqProvider(
        [("shell", {"x": "1"}), ("shell", {"x": "2"}), ("shell", {"x": "3"})],
        partial="Done.",
    )
    out = await _drive([tool], provider)
    assert tool.calls == 3, "fail-fail-success must run all three (never bounced)"
    assert _REFUSAL_MARK not in provider.rendered[2], "the 3rd call must not be bounced"
    assert "ok" in provider.rendered[2], "the successful 3rd call's output is delivered"
    # A succeeding final consequential/write outcome → no honest floor.
    delivered = "".join(c.content for c in out.responses)
    assert "Done." in delivered, (
        f"a fail-fail-success turn must deliver its partial, not a floor. delivered={delivered!r}"
    )


# ---------------------------------------------------------------------------
# FALSIFICATION (b) — failures of X do not open the breaker for Y.
# ---------------------------------------------------------------------------


async def test_breaker_is_per_tool() -> None:
    shell = _ScriptedFailTool("shell", results=[False])
    http = _ScriptedFailTool("http", results=[True])
    calls = [("shell", {"x": "1"}), ("shell", {"x": "2"}), ("shell", {"x": "3"}),
             ("http", {"x": "1"})]
    provider = _SeqProvider(calls, partial="ok")
    await _drive([shell, http], provider)
    assert shell.calls == 3
    assert http.calls == 1, "http must run — shell's failures must not open its breaker"
    assert _REFUSAL_MARK not in provider.rendered[3]
