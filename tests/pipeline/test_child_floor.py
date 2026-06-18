"""E2-S2 — a delegated child inherits the parent's EFFECTIVE bounds as its ceiling floor.

Tests:
1. resolve_owl_bounds returns the registered owl's bounds (unit).
2. delegate_task sets child PipelineState.creation_ceiling to the caller owl's bounds.
3. sessions_spawn sets child PipelineState.creation_ceiling to the invoking owl's
   bounds when an initial_task is supplied.
4. sessions_send sets child PipelineState.creation_ceiling to the invoking owl's
   bounds on the continue-run state.
5. TOCTOU-delegation gap: delegate_task stamps the NARROW context ceiling, not the
   wide current-owl bounds, when the parent owl was widened after creation.
"""

from __future__ import annotations

import json

from stackowl.authz import BoundsSpec
from stackowl.infra.trace import TraceContext
from stackowl.owls.a2a_delegation import A2AResult
from stackowl.owls.concurrency import ConcurrencyGovernor
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.owls.session_registry import SessionRegistry
from stackowl.pipeline.authz_compose import resolve_owl_bounds
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import PipelineState
from stackowl.tools.agents.delegate_task import DelegateTaskTool
from stackowl.tools.agents.sessions_send import SessionsSendTool
from stackowl.tools.agents.sessions_spawn import SessionsSpawnTool
from stackowl.tools.registry import ToolRegistry

# ----------------------------------------------------------------- unit: resolve


def test_resolve_parent_bounds_is_the_child_floor() -> None:
    parent_bounds = BoundsSpec(tools=frozenset({"read_file"}))
    reg = OwlRegistry()
    reg.register(OwlAgentManifest(name="parent", role="r", system_prompt="s",
                                  model_tier="fast", bounds=parent_bounds))
    assert resolve_owl_bounds("parent", reg) == parent_bounds


def test_resolve_unknown_owl_returns_none() -> None:
    reg = OwlRegistry()
    assert resolve_owl_bounds("ghost", reg) is None


def test_resolve_none_registry_returns_none() -> None:
    assert resolve_owl_bounds("anything", None) is None


# ----------------------------------------------------------------- fakes / fixtures

_CALLER_BOUNDS = BoundsSpec(tools=frozenset({"read_file", "write_file"}))


def _reg_with_caller_and_specialist() -> OwlRegistry:
    """Registry with a narrow-bounded 'caller' owl and a broader 'scout' owl."""
    reg = OwlRegistry.with_default_secretary()
    reg.register(OwlAgentManifest(
        name="caller_owl", role="caller-role", system_prompt="I call.",
        model_tier="fast", bounds=_CALLER_BOUNDS,
    ))
    reg.register(OwlAgentManifest(
        name="scout", role="research-scout", system_prompt="I research.",
        model_tier="standard",
        # scout has broader (unbounded) bounds — the floor test shows child is clamped
    ))
    return reg


class _CapturingDelegator:
    """Records the parent_state passed to delegate() so we can inspect creation_ceiling."""

    def __init__(self) -> None:
        self.captured_states: list[PipelineState] = []

    async def delegate(
        self, *, from_owl: str, to_owl: str, sub_task: str, parent_state: PipelineState
    ) -> A2AResult:
        self.captured_states.append(parent_state)
        return A2AResult(status="ok", content="sub-task completed successfully", resolved_owl=to_owl)


class _ScriptedProvider:
    """Minimal provider that completes without a network call."""

    protocol = "anthropic"

    def __init__(self) -> None:
        self.sub_states_seen: list[object] = []

    async def complete_with_tools(self, *, user_text, system_text, tool_schemas,
                                   tool_dispatcher, history=None, **_kwargs):  # noqa: ANN001, ANN204
        return (f"reply to {user_text!r}", [])

    async def complete(self, *a, **k):  # pragma: no cover
        return ""

    async def stream(self, *a, **k):  # pragma: no cover
        if False:
            yield ""


class _ProviderRegistry:
    def __init__(self, p: object) -> None:
        self._p = p

    def get(self, name: str) -> object:
        return self._p

    def get_by_tier(self, tier: str) -> object:
        return self._p


def _record(output: str) -> dict[str, object]:
    return json.loads(output)["record"]


# -------------------------------------------------- delegate_task floor test


async def test_delegate_task_sets_child_ceiling_to_caller_bounds() -> None:
    """The PipelineState passed to A2ADelegator.delegate must carry the caller owl's
    bounds as creation_ceiling — so the child cannot exceed the parent's envelope."""
    delegator = _CapturingDelegator()
    reg = _reg_with_caller_and_specialist()
    services = StepServices(a2a_delegator=delegator, owl_registry=reg)  # type: ignore[arg-type]
    token = set_services(services)
    # The pipeline stamps owl_name into TraceContext; here we simulate caller_owl
    # being the invoking owl (as would happen when it calls delegate_task mid-turn).
    trace = TraceContext.start("sess", trace_id="tr-floor", channel="cli",
                               owl_name="caller_owl")
    try:
        res = await DelegateTaskTool().execute(goal="sub-task", to_owl="scout")
    finally:
        TraceContext.reset(trace)
        reset_services(token)

    assert res.success
    assert len(delegator.captured_states) == 1
    child_state = delegator.captured_states[0]
    # The child's creation_ceiling must equal the CALLER owl's bounds (the floor).
    assert child_state.creation_ceiling == _CALLER_BOUNDS


# -------------------------------------------------- sessions_spawn floor test


async def test_sessions_spawn_sets_child_ceiling_to_invoking_owl_bounds() -> None:
    """When sessions_spawn fires an initial_task, the child PipelineState must
    carry the INVOKING (caller) owl's bounds as creation_ceiling."""
    reg = _reg_with_caller_and_specialist()
    sessions = SessionRegistry()
    provider = _ScriptedProvider()
    services = StepServices(
        provider_registry=_ProviderRegistry(provider),  # type: ignore[arg-type]
        tool_registry=ToolRegistry.with_defaults(),
        session_registry=sessions,
        owl_registry=reg,
        delegation_governor=ConcurrencyGovernor(),
    )

    captured: list[PipelineState] = []

    # Patch AsyncioBackend.run to capture the sub_state before running it.
    import stackowl.tools.agents.sessions_spawn as _spawn_mod
    original_run = _spawn_mod.AsyncioBackend.run  # type: ignore[attr-defined]

    async def _capturing_run(self: object, state: PipelineState) -> PipelineState:
        captured.append(state)
        return await original_run(self, state)

    _spawn_mod.AsyncioBackend.run = _capturing_run  # type: ignore[method-assign]

    token = set_services(services)
    trace = TraceContext.start("sess", trace_id="tr-spawn-floor", channel="cli",
                               owl_name="caller_owl")
    try:
        res = await SessionsSpawnTool().execute(label="session1", owl="scout",
                                                initial_task="say hello")
    finally:
        TraceContext.reset(trace)
        reset_services(token)
        _spawn_mod.AsyncioBackend.run = original_run  # type: ignore[method-assign]

    assert res.success
    # At least one sub_state was captured (the initial_task run).
    assert len(captured) >= 1
    child_state = captured[0]
    # The child's creation_ceiling must equal the INVOKING owl's bounds.
    assert child_state.creation_ceiling == _CALLER_BOUNDS


# -------------------------------------------------- sessions_send floor test


async def test_sessions_send_sets_child_ceiling_to_invoking_owl_bounds() -> None:
    """A sessions_send continue-run PipelineState must carry the INVOKING (caller)
    owl's bounds as creation_ceiling — the session's owl cannot exceed the invoker."""
    reg = _reg_with_caller_and_specialist()
    sessions = SessionRegistry()
    sessions.spawn("worker", "scout")
    provider = _ScriptedProvider()
    services = StepServices(
        provider_registry=_ProviderRegistry(provider),  # type: ignore[arg-type]
        tool_registry=ToolRegistry.with_defaults(),
        session_registry=sessions,
        owl_registry=reg,
        delegation_governor=ConcurrencyGovernor(),
    )

    captured: list[PipelineState] = []

    import stackowl.tools.agents.sessions_send as _send_mod
    original_run = _send_mod.AsyncioBackend.run  # type: ignore[attr-defined]

    async def _capturing_run(self: object, state: PipelineState) -> PipelineState:
        captured.append(state)
        return await original_run(self, state)

    _send_mod.AsyncioBackend.run = _capturing_run  # type: ignore[method-assign]

    token = set_services(services)
    trace = TraceContext.start("sess", trace_id="tr-send-floor", channel="cli",
                               owl_name="caller_owl")
    try:
        res = await SessionsSendTool().execute(label="worker", message="go")
    finally:
        TraceContext.reset(trace)
        reset_services(token)
        _send_mod.AsyncioBackend.run = original_run  # type: ignore[method-assign]

    assert res.success
    assert len(captured) >= 1
    child_state = captured[0]
    # The child's creation_ceiling must equal the INVOKING owl's bounds.
    assert child_state.creation_ceiling == _CALLER_BOUNDS


# --------------------------------- TOCTOU-delegation gap: narrow ceiling wins


_NARROW_CEILING = BoundsSpec(tools=frozenset({"read_file"}))
_WIDE_OWL_BOUNDS = BoundsSpec(tools=frozenset({"read_file", "shell"}))


async def test_delegate_task_toctou_narrow_ceiling_wins() -> None:
    """TOCTOU-delegation gap test: the parent owl is registered with WIDE bounds
    {read_file, shell}, but the context creation_ceiling is NARROW {read_file}
    (as would occur after a durable task resumes with a widened owl).
    The child's creation_ceiling must be the NARROW {read_file} — NOT the wide
    owl bounds. This verifies child_floor uses owl ∩ ceiling = ceiling (narrow wins)."""
    delegator = _CapturingDelegator()
    reg = OwlRegistry.with_default_secretary()
    reg.register(OwlAgentManifest(
        name="caller_owl", role="caller-role", system_prompt="I call.",
        model_tier="fast", bounds=_WIDE_OWL_BOUNDS,
    ))
    reg.register(OwlAgentManifest(
        name="scout", role="research-scout", system_prompt="I research.",
        model_tier="standard",
    ))
    services = StepServices(a2a_delegator=delegator, owl_registry=reg)  # type: ignore[arg-type]
    token = set_services(services)
    # Stamp the parent context with the NARROW persisted ceiling (simulates a resumed
    # durable task whose owl was widened after creation).
    trace = TraceContext.start(
        "sess", trace_id="tr-toctou", channel="cli",
        owl_name="caller_owl",
        creation_ceiling=_NARROW_CEILING,
    )
    try:
        res = await DelegateTaskTool().execute(goal="sub-task", to_owl="scout")
    finally:
        TraceContext.reset(trace)
        reset_services(token)

    assert res.success
    assert len(delegator.captured_states) == 1
    child_state = delegator.captured_states[0]
    # The narrow ceiling must win over the wide owl bounds.
    assert child_state.creation_ceiling is not None
    assert child_state.creation_ceiling.tools == frozenset({"read_file"}), (
        f"Expected narrow ceiling {{read_file}}, got {child_state.creation_ceiling.tools!r} — "
        "TOCTOU-delegation gap: child received the wide owl bounds instead of the narrow ceiling"
    )
