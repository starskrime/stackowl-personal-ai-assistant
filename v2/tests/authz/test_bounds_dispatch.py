"""E2-S1 (FR33) — TOOLS bounds axis is enforced at the dispatch seam.

Drives the real pipeline tool-loop (``_run_with_tools``), mocking ONLY the AI
provider. Proves: an owl whose bounds permit ``allowed_tool`` runs it but is
cleanly BLOCKED from ``forbidden_tool`` (the tool's execute() never runs, no
crash); an owl with bounds=None runs both (byte-for-byte legacy behavior).
"""

from __future__ import annotations

from stackowl.authz import BoundsSpec
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps.execute import _run_with_tools
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.registry import ToolRegistry


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

    async def complete_with_tools(self, *, user_text, system_text, tool_schemas, tool_dispatcher, history=None):  # noqa: ANN001, E501
        self.results["allowed_tool"] = await tool_dispatcher("allowed_tool", {})
        self.results["forbidden_tool"] = await tool_dispatcher("forbidden_tool", {})
        return ("done", [])


def _state() -> PipelineState:
    return PipelineState(
        trace_id="trace-bounds", session_id="sess-1", input_text="go",
        channel="telegram", owl_name="bounded_owl", pipeline_step="execute",
    )


def _manifest(name: str, bounds: BoundsSpec | None) -> OwlAgentManifest:
    return OwlAgentManifest(
        name=name, role="r", system_prompt="s", model_tier="fast", bounds=bounds,
    )


class _RecordingConsentGate:
    """Records tool names passed to check(); always approves.

    Matches the real ConsequentialActionGate.check() positional + keyword
    signature as called by the dispatch seam:
      gate.check(t, channel=..., session_id=..., call_args=...)
    """

    def __init__(self) -> None:
        self.checked: list[str] = []

    async def check(
        self,
        tool: object,
        *,
        channel: str | None = None,
        session_id: str | None = None,
        category: str | None = None,
        call_args: dict[str, object] | None = None,
    ) -> bool:
        from stackowl.tools.base import Tool

        if isinstance(tool, Tool):
            self.checked.append(tool.manifest.name)
        return True


async def _drive(
    bounds: BoundsSpec | None,
    *,
    ceiling: BoundsSpec | None = None,
    consent_gate: object = None,
) -> tuple[_RecordingTool, _RecordingTool, _TwoToolProvider]:
    allowed = _RecordingTool("allowed_tool")
    forbidden = _RecordingTool("forbidden_tool")
    registry = ToolRegistry()
    registry.register(allowed)
    registry.register(forbidden)
    owl_registry = OwlRegistry()
    owl_registry.register(_manifest("bounded_owl", bounds))
    provider = _TwoToolProvider()
    state = _state()
    if ceiling is not None:
        state = state.evolve(creation_ceiling=ceiling)
    services_kwargs: dict[str, object] = dict(tool_registry=registry, owl_registry=owl_registry)
    if consent_gate is not None:
        services_kwargs["consent_gate"] = consent_gate
    token = set_services(StepServices(**services_kwargs))  # type: ignore[arg-type]
    try:
        await _run_with_tools(state, provider, registry)  # type: ignore[arg-type]
    finally:
        reset_services(token)
    return allowed, forbidden, provider


async def test_bounds_blocks_forbidden_tool_and_runs_allowed() -> None:
    bounds = BoundsSpec(tools=frozenset({"allowed_tool"}))
    allowed, forbidden, provider = await _drive(bounds)

    # allowed tool ran
    assert allowed.executed is True
    assert provider.results["allowed_tool"] == "RAN:allowed_tool"

    # forbidden tool was cleanly blocked — never executed, no crash
    assert forbidden.executed is False
    assert "RAN:forbidden_tool" not in provider.results["forbidden_tool"]
    assert "not permitted by this owl's bounds" in provider.results["forbidden_tool"]


async def test_no_bounds_runs_both_unchanged() -> None:
    """bounds=None → both tools run (legacy behavior, byte-for-byte)."""
    allowed, forbidden, provider = await _drive(None)
    assert allowed.executed is True
    assert forbidden.executed is True
    assert provider.results["allowed_tool"] == "RAN:allowed_tool"
    assert provider.results["forbidden_tool"] == "RAN:forbidden_tool"


async def test_none_tools_axis_runs_both() -> None:
    """A BoundsSpec with tools=None (other axes set) still permits all tools."""
    bounds = BoundsSpec(data_owner_id="owner-1")  # tools axis is None
    allowed, forbidden, provider = await _drive(bounds)
    assert allowed.executed is True
    assert forbidden.executed is True


# --- Murat probe 4: loop prevention — a forbidden tool re-called is short-circuited


class _RepeatForbiddenProvider:
    """Calls ``forbidden_tool`` TWICE — proving the second call short-circuits
    via denied_this_run rather than re-running the full bounds check (no loop)."""

    protocol = "anthropic"

    def __init__(self) -> None:
        self.results: list[str] = []

    async def complete_with_tools(self, *, user_text, system_text, tool_schemas, tool_dispatcher, history=None):  # noqa: ANN001, E501
        self.results.append(await tool_dispatcher("forbidden_tool", {}))
        self.results.append(await tool_dispatcher("forbidden_tool", {}))
        return ("done", [])


async def test_bounds_block_records_denied_this_run_and_short_circuits_repeat() -> None:
    """A forbidden tool called twice is blocked BOTH times; the first block records
    it in denied_this_run so the second is the stable short-circuit (no loop)."""
    forbidden = _RecordingTool("forbidden_tool")
    registry = ToolRegistry()
    registry.register(forbidden)
    owl_registry = OwlRegistry()
    owl_registry.register(_manifest("bounded_owl", BoundsSpec(tools=frozenset({"allowed_tool"}))))
    provider = _RepeatForbiddenProvider()
    token = set_services(StepServices(tool_registry=registry, owl_registry=owl_registry))
    try:
        await _run_with_tools(_state(), provider, registry)  # type: ignore[arg-type]
    finally:
        reset_services(token)

    # blocked BOTH times — the tool's execute never ran.
    assert forbidden.executed is False
    assert len(provider.results) == 2
    # 1st call: the bounds block reason.
    assert "not permitted by this owl's bounds" in provider.results[0]
    # 2nd call: the stable "already declined this turn" short-circuit (proves the
    # block was recorded in denied_this_run — no fresh full bounds-check loop).
    assert "already declined this turn" in provider.results[1]


# --- Murat probe 3: empty-allowlist footgun — fail-closed denies discovery too ---


class _MetaToolProvider:
    """Tries the discovery meta-tools tool_search / tool_describe."""

    protocol = "anthropic"

    def __init__(self) -> None:
        self.results: dict[str, str] = {}

    async def complete_with_tools(self, *, user_text, system_text, tool_schemas, tool_dispatcher, history=None):  # noqa: ANN001, E501
        self.results["tool_search"] = await tool_dispatcher("tool_search", {})
        self.results["tool_describe"] = await tool_dispatcher("tool_describe", {})
        return ("done", [])


async def test_empty_allowlist_blocks_even_discovery_meta_tools() -> None:
    """bounds=BoundsSpec(tools=frozenset()) is fail-closed: it denies ALL tools,
    INCLUDING the discovery meta-tools tool_search / tool_describe, with a
    non-empty clean reason (no auto-exemption — a documented builder-time concern)."""
    search = _RecordingTool("tool_search")
    describe = _RecordingTool("tool_describe")
    registry = ToolRegistry()
    registry.register(search)
    registry.register(describe)
    owl_registry = OwlRegistry()
    owl_registry.register(_manifest("locked_owl", BoundsSpec(tools=frozenset())))
    provider = _MetaToolProvider()
    state = PipelineState(
        trace_id="trace-locked", session_id="sess-2", input_text="go",
        channel="telegram", owl_name="locked_owl", pipeline_step="execute",
    )
    token = set_services(StepServices(tool_registry=registry, owl_registry=owl_registry))
    try:
        await _run_with_tools(state, provider, registry)  # type: ignore[arg-type]
    finally:
        reset_services(token)

    # NEITHER meta-tool ran — fail-closed.
    assert search.executed is False
    assert describe.executed is False
    # Both got a NON-EMPTY clean bounds reason.
    for name in ("tool_search", "tool_describe"):
        reason = provider.results[name]
        assert reason.strip() != ""
        assert "not permitted by this owl's bounds" in reason


# --- E2-S2: ceiling narrows the effective bounds below the owl's own bounds ---


async def test_ceiling_narrows_below_owl_bounds() -> None:
    """A creation_ceiling tighter than the owl's own bounds is enforced at dispatch.

    The owl permits {allowed_tool, forbidden_tool}; the ceiling restricts to
    {allowed_tool} only. Effective = owl ∩ ceiling = {allowed_tool}, so
    forbidden_tool must be blocked even though the owl itself allows it.
    """
    owl_bounds = BoundsSpec(tools=frozenset({"allowed_tool", "forbidden_tool"}))
    ceiling = BoundsSpec(tools=frozenset({"allowed_tool"}))
    allowed, forbidden, provider = await _drive(owl_bounds, ceiling=ceiling)
    assert allowed.executed is True
    assert forbidden.executed is False
    assert "not permitted by this owl's bounds" in provider.results["forbidden_tool"]


async def test_out_of_bounds_tool_never_reaches_consent() -> None:
    """A tool blocked by effective bounds is refused BEFORE the consent gate is consulted.

    The bounds check short-circuits in _dispatch before the gate.check() call,
    so the recording gate must never see the forbidden_tool name.
    Note: _RecordingTool uses action_severity="read" so the gate would not fire
    anyway for a read-severity tool — but the point of this test is that bounds
    returns BEFORE the gate code path is reached at all, regardless of severity.
    """
    owl_bounds = BoundsSpec(tools=frozenset({"allowed_tool", "forbidden_tool"}))
    ceiling = BoundsSpec(tools=frozenset({"allowed_tool"}))
    gate = _RecordingConsentGate()
    allowed, forbidden, provider = await _drive(owl_bounds, ceiling=ceiling, consent_gate=gate)
    assert forbidden.executed is False
    assert "forbidden_tool" not in gate.checked
