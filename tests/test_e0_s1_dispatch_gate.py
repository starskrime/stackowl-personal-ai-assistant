"""E0-S1 integration — the consent gate is actually invoked in tool dispatch.

This is the B1 fix: prove a consequential tool dispatched through the real
pipeline tool-loop is BLOCKED when consent is denied and RUNS when granted,
and that a non-consequential tool is never gated.
"""

from __future__ import annotations

from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps.execute import _run_with_tools
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.consent import ConsentPolicy, ConsentRequest, ConsentScope
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry


class _ScopePrompter:
    def __init__(self, scope: ConsentScope) -> None:
        self._scope = scope

    async def prompt(self, req: ConsentRequest) -> ConsentScope:
        return self._scope


class _RecordingTool(Tool):
    """Consequential tool that records whether it actually executed."""

    def __init__(self, name: str = "danger", severity: str = "consequential") -> None:
        self._name = name
        self._severity = severity
        self.executed = False

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return "Records execution."

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {}}

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self._name, description=self.description,
            parameters=self.parameters, action_severity=self._severity,  # type: ignore[arg-type]
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        self.executed = True
        return ToolResult(success=True, output="EXECUTED", duration_ms=1.0)


class _FakeProvider:
    """Provider whose tool loop dispatches the recording tool exactly once."""

    protocol = "anthropic"

    def __init__(self, tool_name: str = "danger") -> None:
        self.tool_name = tool_name
        self.dispatch_result: str | None = None

    async def complete_with_tools(
        self, *, user_text, system_text, tool_schemas,
        tool_dispatcher, history=None, **_kwargs,
    ):
        self.dispatch_result = await tool_dispatcher(self.tool_name, {})
        return ("done", [{"name": self.tool_name, "args": {}, "result": self.dispatch_result}])


class _RepeatingProvider:
    """Provider that re-calls the same tool twice in ONE run (model retry loop)."""

    protocol = "anthropic"

    def __init__(self, tool_name: str = "danger") -> None:
        self.tool_name = tool_name
        self.results: list[str] = []

    async def complete_with_tools(
        self, *, user_text, system_text, tool_schemas,
        tool_dispatcher, history=None, **_kwargs,
    ):
        self.results.append(await tool_dispatcher(self.tool_name, {}))
        self.results.append(await tool_dispatcher(self.tool_name, {}))
        return ("done", [])


def _state() -> PipelineState:
    return PipelineState(
        trace_id="trace-1", session_id="sess-1", input_text="run it",
        channel="telegram", owl_name="owl", pipeline_step="execute",
    )


async def _drive(tool: _RecordingTool, gate: ConsequentialActionGate) -> _FakeProvider:
    registry = ToolRegistry()
    registry.register(tool)
    provider = _FakeProvider(tool.name)
    token = set_services(StepServices(consent_gate=gate, tool_registry=registry))
    try:
        await _run_with_tools(_state(), provider, registry)  # type: ignore[arg-type]
    finally:
        reset_services(token)
    return provider


async def test_denied_consequential_tool_is_blocked() -> None:
    tool = _RecordingTool()
    gate = ConsequentialActionGate(ConsentPolicy(prompter=_ScopePrompter(ConsentScope.DENY)))
    provider = await _drive(tool, gate)
    assert tool.executed is False
    assert "EXECUTED" not in (provider.dispatch_result or "")


async def test_granted_consequential_tool_runs() -> None:
    tool = _RecordingTool()
    gate = ConsequentialActionGate(ConsentPolicy(prompter=_ScopePrompter(ConsentScope.ONCE)))
    provider = await _drive(tool, gate)
    assert tool.executed is True
    assert provider.dispatch_result == "EXECUTED"


async def test_nonconsequential_tool_not_gated() -> None:
    tool = _RecordingTool(name="reader", severity="read")
    # gate would DENY if consulted; a read tool must bypass it entirely
    gate = ConsequentialActionGate(ConsentPolicy(prompter=_ScopePrompter(ConsentScope.DENY)))
    provider = await _drive(tool, gate)
    assert tool.executed is True
    assert provider.dispatch_result == "EXECUTED"


async def test_no_gate_service_allows_read_tool() -> None:
    """A read tool must still run when no consent_gate is wired (degraded)."""
    tool = _RecordingTool(name="reader", severity="read")
    registry = ToolRegistry()
    registry.register(tool)
    provider = _FakeProvider("reader")
    token = set_services(StepServices(tool_registry=registry))  # no consent_gate
    try:
        await _run_with_tools(_state(), provider, registry)  # type: ignore[arg-type]
    finally:
        reset_services(token)
    assert provider.dispatch_result == "EXECUTED"


async def test_no_gate_service_fails_closed_for_consequential() -> None:
    """Degraded path: a consequential tool with NO gate wired must NOT run."""
    tool = _RecordingTool()  # consequential
    registry = ToolRegistry()
    registry.register(tool)
    provider = _FakeProvider("danger")
    token = set_services(StepServices(tool_registry=registry))  # no consent_gate
    try:
        await _run_with_tools(_state(), provider, registry)  # type: ignore[arg-type]
    finally:
        reset_services(token)
    assert tool.executed is False
    assert "EXECUTED" not in (provider.dispatch_result or "")


async def test_denied_tool_recalled_same_run_does_not_reprompt() -> None:
    """F3.1 — a model re-calling a just-denied tool in the same run is short-circuited."""

    class _CountingPrompter:
        def __init__(self) -> None:
            self.calls = 0

        async def prompt(self, req: ConsentRequest) -> ConsentScope:
            self.calls += 1
            return ConsentScope.DENY

    from stackowl.tools.consent import ConsentPolicy

    prompter = _CountingPrompter()
    tool = _RecordingTool()
    registry = ToolRegistry()
    registry.register(tool)
    provider = _RepeatingProvider("danger")
    gate = ConsequentialActionGate(ConsentPolicy(prompter=prompter))
    token = set_services(StepServices(consent_gate=gate, tool_registry=registry))
    try:
        await _run_with_tools(_state(), provider, registry)  # type: ignore[arg-type]
    finally:
        reset_services(token)
    assert prompter.calls == 1  # second call short-circuited, no re-prompt
    assert tool.executed is False


async def test_denied_consequential_tool_writes_audit_row() -> None:
    """AC#1 — a blocked consequential action is recorded in the audit log."""
    from stackowl.tools.consent import ConsentPolicy

    rows: list[dict] = []

    class _Audit:
        def append(self, event_type, actor, target, details):  # noqa: ANN001
            rows.append({"event_type": event_type, "target": target, "details": details})

    tool = _RecordingTool()
    gate = ConsequentialActionGate(
        ConsentPolicy(prompter=_ScopePrompter(ConsentScope.DENY), audit_logger=_Audit())
    )
    await _drive(tool, gate)
    assert any(r["event_type"] == "consent.decision" and r["details"]["decision"] == "deny" for r in rows)
