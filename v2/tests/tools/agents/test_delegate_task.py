"""Tests for DelegateTaskTool (E8-S1) — depth backstop, width cap, timeout, footer.

Network-free: a FAKE A2ADelegator records calls and returns canned results. The
real OwlRegistry (secretary + a registered specialist) drives target resolution.
TraceContext is set via TraceContext.start(...) so the tool reads depth/trace.
"""

from __future__ import annotations

import json

import pytest

from stackowl.infra.trace import TraceContext
from stackowl.owls.a2a_delegation import A2AResult
from stackowl.owls.delegation_limits import (
    MAX_CONCURRENT_DELEGATIONS,
    MAX_DELEGATION_DEPTH,
)
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import PipelineState
from stackowl.tools.agents.delegate_task import DelegateTaskTool
from stackowl.tools.registry import ToolRegistry

# ----------------------------------------------------------------- fakes/fixtures


class _FakeDelegator:
    """Records delegate() calls and returns a canned A2AResult."""

    def __init__(self, result: A2AResult | None = None) -> None:
        self.result: A2AResult = result or A2AResult(
            status="ok", content="specialist answer", resolved_owl="scout"
        )
        self.calls: list[dict[str, object]] = []

    async def delegate(
        self, *, from_owl: str, to_owl: str, sub_task: str, parent_state: PipelineState
    ) -> A2AResult:
        self.calls.append(
            {
                "from_owl": from_owl,
                "to_owl": to_owl,
                "sub_task": sub_task,
                "depth": parent_state.delegation_depth,
            }
        )
        return self.result


def _registry_with_specialist() -> OwlRegistry:
    reg = OwlRegistry.with_default_secretary()
    reg.register(
        OwlAgentManifest(
            name="scout",
            role="research-scout",
            system_prompt="You research things.",
            model_tier="standard",
        )
    )
    return reg


def _services(delegator: object | None, registry: OwlRegistry | None) -> StepServices:
    return StepServices(a2a_delegator=delegator, owl_registry=registry)  # type: ignore[arg-type]


def _record(res_output: str) -> dict[str, object]:
    return json.loads(res_output)["record"]


# ----------------------------------------------------- M1: true-caller attribution


async def test_non_secretary_caller_attributed_and_no_self_delegation() -> None:
    """A non-secretary owl delegating: from_owl is the REAL caller (not hardcoded
    'secretary'), and default resolution never returns the caller itself (M1)."""
    reg = _registry_with_specialist()  # secretary + scout
    reg.register(
        OwlAgentManifest(
            name="analyst", role="data-analyst",
            system_prompt="You analyse data.", model_tier="standard",
        )
    )
    fake = _FakeDelegator(A2AResult(status="ok", content="done", resolved_owl="analyst"))
    token = set_services(_services(fake, reg))
    # The pipeline propagates state.owl_name → TraceContext.owl_name; simulate scout.
    trace = TraceContext.start("s", trace_id="tr-caller", channel="cli", owl_name="scout")
    try:
        res = await DelegateTaskTool().execute(goal="analyse this")  # no to_owl → resolve
    finally:
        TraceContext.reset(trace)
        reset_services(token)
    assert res.success
    assert len(fake.calls) == 1
    assert fake.calls[0]["from_owl"] == "scout"  # TRUE caller, not "secretary"
    assert fake.calls[0]["to_owl"] != "scout"  # never self-delegates


# -------------------------------------------------------------------- happy path


async def test_happy_returns_result_with_provenance_footer() -> None:
    fake = _FakeDelegator(A2AResult(status="ok", content="the answer", resolved_owl="scout"))
    token = set_services(_services(fake, _registry_with_specialist()))
    trace = TraceContext.start("sess-1", trace_id="tr-1", channel="cli")
    try:
        res = await DelegateTaskTool().execute(goal="find X", to_owl="scout")
    finally:
        TraceContext.reset(trace)
        reset_services(token)

    assert res.success
    record = _record(res.output)
    assert record["status"] == "ok"
    assert record["to_owl"] == "scout"
    assert "the answer" in str(record["result"])
    # Provenance footer names the delegate owl and flags the sub-run.
    assert "scout" in str(record["result"])
    assert "delegated" in str(record["result"]).lower()
    assert len(fake.calls) == 1
    assert fake.calls[0]["to_owl"] == "scout"


async def test_target_resolved_by_role_when_no_to_owl() -> None:
    fake = _FakeDelegator(A2AResult(status="ok", content="result", resolved_owl="scout"))
    token = set_services(_services(fake, _registry_with_specialist()))
    trace = TraceContext.start("s", trace_id="tr-role", channel="cli")
    try:
        res = await DelegateTaskTool().execute(goal="research", role="research-scout")
    finally:
        TraceContext.reset(trace)
        reset_services(token)

    assert res.success
    assert _record(res.output)["to_owl"] == "scout"


# -------------------------------------------------------------------- depth backstop


async def test_depth_backstop_refuses_without_calling_delegate() -> None:
    fake = _FakeDelegator(A2AResult(status="ok", content="x", resolved_owl="scout"))
    token = set_services(_services(fake, _registry_with_specialist()))
    trace = TraceContext.start(
        "s", trace_id="tr-depth", channel="cli", delegation_depth=MAX_DELEGATION_DEPTH
    )
    try:
        res = await DelegateTaskTool().execute(goal="go deeper", to_owl="scout")
    finally:
        TraceContext.reset(trace)
        reset_services(token)

    assert res.success  # structured refusal, not a crash
    record = _record(res.output)
    assert record["status"] == "refused"
    assert record["reason"] == "depth_limit"
    # delegate() was NEVER called.
    assert fake.calls == []


# --------------------------------------------------------------------- width cap


async def test_width_cap_refuses_fifth_concurrent_and_counter_decrements() -> None:
    tool = DelegateTaskTool()
    trace_id = "tr-width"
    # Saturate: occupy all MAX_CONCURRENT_DELEGATIONS slots for this trace.
    for _ in range(MAX_CONCURRENT_DELEGATIONS):
        assert tool._try_acquire(trace_id) is True
    # The (cap+1)th is refused.
    assert tool._try_acquire(trace_id) is False

    fake = _FakeDelegator(A2AResult(status="ok", content="x", resolved_owl="scout"))
    token = set_services(_services(fake, _registry_with_specialist()))
    trace = TraceContext.start("s", trace_id=trace_id, channel="cli")
    try:
        res = await tool.execute(goal="overflow", to_owl="scout")
    finally:
        TraceContext.reset(trace)
        reset_services(token)

    assert res.success
    record = _record(res.output)
    assert record["status"] == "refused"
    assert record["reason"] == "width_limit"
    assert fake.calls == []  # delegate not called past the cap

    # Counter decrements: release all held slots → key drops, a new acquire works.
    for _ in range(MAX_CONCURRENT_DELEGATIONS):
        tool._release(trace_id)
    assert trace_id not in tool._active
    assert tool._try_acquire(trace_id) is True


async def test_width_slot_released_after_successful_delegation() -> None:
    fake = _FakeDelegator(A2AResult(status="ok", content="x", resolved_owl="scout"))
    tool = DelegateTaskTool()
    token = set_services(_services(fake, _registry_with_specialist()))
    trace = TraceContext.start("s", trace_id="tr-rel", channel="cli")
    try:
        await tool.execute(goal="task", to_owl="scout")
    finally:
        TraceContext.reset(trace)
        reset_services(token)
    # finally-block decremented the slot back to zero (key removed).
    assert "tr-rel" not in tool._active


# ------------------------------------------------------------------ timeout/empty


async def test_empty_result_becomes_structured_empty_status() -> None:
    # A2ADelegator now returns A2AResult(status="empty") for a no-content response.
    fake = _FakeDelegator(A2AResult(status="empty", content="", resolved_owl="scout"))
    token = set_services(_services(fake, _registry_with_specialist()))
    trace = TraceContext.start("s", trace_id="tr-empty", channel="cli")
    try:
        res = await DelegateTaskTool().execute(goal="task", to_owl="scout")
    finally:
        TraceContext.reset(trace)
        reset_services(token)

    assert res.success
    record = _record(res.output)
    assert record["status"] == "empty"  # refined from old "timeout_or_empty"
    assert record["result"] == ""  # NOT a bare empty string at the tool boundary
    # T7 recovery ladder: "empty" is retriable, so delegate is called twice
    # (initial attempt + retry-once). Fallback to secretary is skipped because
    # caller==secretary. Two calls is the correct bounded behavior.
    assert len(fake.calls) == 2  # initial + retry (fallback skipped: caller is secretary)


# -------------------------------------------------------------- self-healing edges


async def test_no_delegator_wired_is_structured_not_raised() -> None:
    token = set_services(_services(None, _registry_with_specialist()))
    trace = TraceContext.start("s", trace_id="tr-none", channel="cli")
    try:
        res = await DelegateTaskTool().execute(goal="task", to_owl="scout")
    finally:
        TraceContext.reset(trace)
        reset_services(token)

    assert res.success
    assert _record(res.output)["reason"] == "unavailable"


async def test_named_missing_owl_is_target_not_found() -> None:
    """An explicit to_owl that does not exist in the registry → target_not_found (not refused)."""
    fake = _FakeDelegator(A2AResult(status="ok", content="x", resolved_owl="scout"))
    token = set_services(_services(fake, _registry_with_specialist()))
    trace = TraceContext.start("s", trace_id="tr-unres", channel="cli")
    try:
        res = await DelegateTaskTool().execute(goal="task", to_owl="ghost")
    finally:
        TraceContext.reset(trace)
        reset_services(token)

    assert res.success
    record = _record(res.output)
    assert record["status"] == "target_not_found"
    assert fake.calls == []


async def test_no_candidate_specialist_is_structured_refusal() -> None:
    """Registry with only the caller → no non-caller candidate → refused/unresolved_target."""
    fake = _FakeDelegator(A2AResult(status="ok", content="x", resolved_owl="scout"))
    # Registry with ONLY the secretary (caller) → no non-caller specialist.
    token = set_services(_services(fake, OwlRegistry.with_default_secretary()))
    trace = TraceContext.start("s", trace_id="tr-nocandidate", channel="cli")
    try:
        res = await DelegateTaskTool().execute(goal="task", role="nobody")
    finally:
        TraceContext.reset(trace)
        reset_services(token)

    assert res.success
    record = _record(res.output)
    assert record["status"] == "refused"
    assert record["reason"] == "unresolved_target"
    assert fake.calls == []


async def test_delegate_raising_is_caught_as_structured_error() -> None:
    class _Boom:
        async def delegate(self, **_kwargs: object) -> str:
            raise RuntimeError("kaboom")

    token = set_services(_services(_Boom(), _registry_with_specialist()))
    trace = TraceContext.start("s", trace_id="tr-boom", channel="cli")
    try:
        res = await DelegateTaskTool().execute(goal="task", to_owl="scout")
    finally:
        TraceContext.reset(trace)
        reset_services(token)

    assert res.success
    assert _record(res.output)["status"] == "error"


# ------------------------------------------------------------------ schema/manifest


async def test_invalid_args_rejected() -> None:
    res = await DelegateTaskTool().execute(goal="x", bogus="y")
    assert res.success is False
    assert "invalid arguments" in (res.error or "")


def test_manifest_is_write_severity_in_agents_group() -> None:
    m = DelegateTaskTool().manifest
    assert m.name == "delegate_task"
    assert m.action_severity == "write"
    assert m.toolset_group == "agents"


def test_registered_in_with_defaults() -> None:
    tool = ToolRegistry.with_defaults().get("delegate_task")
    assert isinstance(tool, DelegateTaskTool)


# ---------------------------------------------------- TraceContext depth propagation


def test_trace_context_propagates_delegation_depth() -> None:
    trace = TraceContext.start("s", trace_id="tr-prop", delegation_depth=1)
    try:
        assert TraceContext.get()["delegation_depth"] == 1
    finally:
        TraceContext.reset(trace)
    # Default restored after reset.
    assert TraceContext.get()["delegation_depth"] == 0


async def test_backend_propagates_state_depth_to_trace_context() -> None:
    """A state with delegation_depth=1 must surface as depth 1 inside a step."""
    from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend

    seen: dict[str, int] = {}

    async def _probe(state: PipelineState) -> PipelineState:
        seen["depth"] = int(TraceContext.get()["delegation_depth"])
        return state

    import stackowl.pipeline.backends.asyncio_backend as ab

    original = ab.PIPELINE_STEPS
    ab.PIPELINE_STEPS = (("probe", _probe),)  # type: ignore[assignment]
    try:
        state = PipelineState(
            trace_id="tr-be",
            session_id="s",
            input_text="x",
            channel="cli",
            owl_name="secretary",
            pipeline_step="start",
            delegation_depth=1,
        )
        await AsyncioBackend().run(state)
    finally:
        ab.PIPELINE_STEPS = original  # type: ignore[assignment]

    assert seen["depth"] == 1


# ------------------------------------------------------------ T5: cycle / target_not_found / child_error


@pytest.mark.asyncio
async def test_cycle_refused_before_spawn() -> None:
    """Cycle detected after resolve — refused BEFORE width-acquire (fake.calls == [])."""
    fake = _FakeDelegator(A2AResult(status="ok", content="x", resolved_owl="scout"))
    token = set_services(_services(fake, _registry_with_specialist()))
    trace = TraceContext.start("s", trace_id="t", channel="cli",
                               owl_name="secretary", delegation_chain=("scout",))
    try:
        res = await DelegateTaskTool().execute(goal="g", to_owl="scout")
    finally:
        TraceContext.reset(trace)
        reset_services(token)
    rec = _record(res.output)
    assert rec["status"] == "cycle"
    assert fake.calls == []  # refused PRE-spawn


@pytest.mark.asyncio
async def test_target_not_found_distinct_no_spawn() -> None:
    """A named owl that does not exist → target_not_found, no spawn."""
    fake = _FakeDelegator(A2AResult(status="ok", content="x", resolved_owl="x"))
    token = set_services(_services(fake, _registry_with_specialist()))
    trace = TraceContext.start("s", trace_id="t", channel="cli", owl_name="secretary")
    try:
        res = await DelegateTaskTool().execute(goal="g", to_owl="ghost")
    finally:
        TraceContext.reset(trace)
        reset_services(token)
    rec = _record(res.output)
    assert rec["status"] == "target_not_found"
    assert fake.calls == []


@pytest.mark.asyncio
async def test_child_error_status_mapped() -> None:
    """A2AResult(status='child_error') maps to record status 'child_error'."""
    fake = _FakeDelegator(A2AResult(status="child_error", child_detail="boom", resolved_owl="scout"))
    token = set_services(_services(fake, _registry_with_specialist()))
    trace = TraceContext.start("s", trace_id="t", channel="cli", owl_name="secretary")
    try:
        res = await DelegateTaskTool().execute(goal="g", to_owl="scout")
    finally:
        TraceContext.reset(trace)
        reset_services(token)
    rec = _record(res.output)
    assert rec["status"] == "child_error"


# -------------------------------------------------------- T7: bounded recovery ladder


class _ScriptedDelegator:
    """Returns successive A2AResults from a script list and records every call."""

    def __init__(self, results: list[A2AResult]) -> None:
        self._results = list(results)
        self.calls: list[dict[str, object]] = []

    async def delegate(
        self, *, from_owl: str, to_owl: str, sub_task: str, parent_state: PipelineState
    ) -> A2AResult:
        self.calls.append({"to_owl": to_owl, "depth": parent_state.delegation_depth})
        return self._results.pop(0)


def _registry_with_three_owls() -> OwlRegistry:
    """Secretary + scout + analyst — needed for fallback tests where caller != secretary."""
    reg = OwlRegistry.with_default_secretary()
    reg.register(
        OwlAgentManifest(
            name="scout",
            role="research-scout",
            system_prompt="You research things.",
            model_tier="standard",
        )
    )
    reg.register(
        OwlAgentManifest(
            name="analyst",
            role="data-analyst",
            system_prompt="You analyse data.",
            model_tier="standard",
        )
    )
    return reg


@pytest.mark.asyncio
async def test_retry_then_fallback_recovers() -> None:
    """Retry-once + fallback-to-secretary: 3 calls total, recovered_via_secretary status.

    Scenario: caller=scout delegates to analyst (fails twice) → fallback to secretary (ok).
    Depth is IDENTICAL across all 3 attempts (parent_state reused; no re-increment).
    """
    fake = _ScriptedDelegator([
        A2AResult(status="timeout", resolved_owl="analyst"),
        A2AResult(status="timeout", resolved_owl="analyst"),
        A2AResult(status="ok", content="done", resolved_owl="secretary"),
    ])
    reg = _registry_with_three_owls()
    token = set_services(_services(fake, reg))
    # caller=scout (non-secretary) delegates to analyst; fallback will go to secretary
    trace = TraceContext.start("s", trace_id="t-ladder", channel="cli", owl_name="scout")
    try:
        res = await DelegateTaskTool().execute(goal="g", to_owl="analyst")
    finally:
        TraceContext.reset(trace)
        reset_services(token)

    rec = _record(res.output)
    assert rec["status"] == "recovered_via_secretary"
    assert "done" in str(rec["result"])
    assert len(fake.calls) == 3  # initial + retry + fallback
    assert [c["to_owl"] for c in fake.calls] == ["analyst", "analyst", "secretary"]
    # Depth must be identical across all attempts (parent_state NOT re-built per attempt).
    depths = [c["depth"] for c in fake.calls]
    assert depths[0] == depths[1] == depths[2], f"depths differ: {depths}"


@pytest.mark.asyncio
async def test_fallback_skipped_when_caller_is_secretary() -> None:
    """Secretary is the caller → no self-fallback; only initial + retry (2 calls max)."""
    fake = _ScriptedDelegator([
        A2AResult(status="child_error", resolved_owl="scout"),
        A2AResult(status="child_error", resolved_owl="scout"),
    ])
    token = set_services(_services(fake, _registry_with_three_owls()))
    trace = TraceContext.start("s", trace_id="t-noselffall", channel="cli", owl_name="secretary")
    try:
        res = await DelegateTaskTool().execute(goal="g", to_owl="scout")
    finally:
        TraceContext.reset(trace)
        reset_services(token)

    rec = _record(res.output)
    # No fallback fires; final status is the terminal child_error.
    assert rec["status"] == "child_error"
    assert len(fake.calls) == 2  # initial + retry, NO fallback
    assert all(c["to_owl"] == "scout" for c in fake.calls)


@pytest.mark.asyncio
async def test_fallback_skipped_when_secretary_in_chain() -> None:
    """Secretary already in delegation chain → fallback would create a cycle; skip it."""
    fake = _ScriptedDelegator([
        A2AResult(status="child_error", resolved_owl="analyst"),
        A2AResult(status="child_error", resolved_owl="analyst"),
    ])
    token = set_services(_services(fake, _registry_with_three_owls()))
    # secretary is in the chain → fallback must be skipped
    trace = TraceContext.start(
        "s", trace_id="t-inchain", channel="cli",
        owl_name="scout", delegation_chain=("secretary",),
    )
    try:
        res = await DelegateTaskTool().execute(goal="g", to_owl="analyst")
    finally:
        TraceContext.reset(trace)
        reset_services(token)

    rec = _record(res.output)
    # Falls through to honest terminal — no fallback attempted.
    assert rec["status"] == "child_error"
    assert len(fake.calls) == 2  # initial + retry only
