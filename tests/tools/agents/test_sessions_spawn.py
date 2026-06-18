"""Tests for SessionsSpawnTool (E8-S3) — spawn, dup/cap refusal, depth gate.

Network-free: the tool calls ``.execute()`` directly (bypassing __call__ /
TestModeGuard). A REAL SessionRegistry + OwlRegistry are injected via StepServices;
TraceContext is set via TraceContext.start(...) so the tool reads trace/owl. The
S0 depth gate is cross-checked against the execute step's _CHILD_EXCLUDED_TOOLS.
"""

from __future__ import annotations

import json

from stackowl.infra.trace import TraceContext
from stackowl.owls.delegation_limits import MAX_LIVE_SESSIONS
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.owls.session_registry import SessionRegistry
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.steps.execute import _CHILD_EXCLUDED_TOOLS
from stackowl.tools.agents.sessions_spawn import SessionsSpawnTool


def _registry_with_specialist() -> OwlRegistry:
    reg = OwlRegistry.with_default_secretary()
    reg.register(
        OwlAgentManifest(
            name="scout", role="research-scout",
            system_prompt="You research.", model_tier="standard",
        )
    )
    return reg


def _record(res_output: str) -> dict[str, object]:
    return json.loads(res_output)["record"]


async def test_spawn_via_tool_returns_handle_and_registers() -> None:
    sessions = SessionRegistry()
    services = StepServices(session_registry=sessions, owl_registry=_registry_with_specialist())
    token = set_services(services)
    trace = TraceContext.start("s", trace_id="tr1", channel="cli", owl_name="secretary")
    try:
        res = await SessionsSpawnTool().execute(label="researcher", owl="scout")
    finally:
        TraceContext.reset(trace)
        reset_services(token)
    assert res.success
    rec = _record(res.output)
    assert rec["status"] == "spawned"
    assert rec["label"] == "researcher"
    assert rec["owl"] == "scout"
    # The session is REALLY in the registry, addressable by label.
    handle = sessions.get("researcher")
    assert handle is not None
    assert handle.owl_name == "scout"


async def test_duplicate_label_surfaces_structured_refusal() -> None:
    sessions = SessionRegistry()
    sessions.spawn("dup", "scout")
    services = StepServices(session_registry=sessions, owl_registry=_registry_with_specialist())
    token = set_services(services)
    trace = TraceContext.start("s", trace_id="tr", channel="cli", owl_name="secretary")
    try:
        res = await SessionsSpawnTool().execute(label="dup", owl="scout")
    finally:
        TraceContext.reset(trace)
        reset_services(token)
    assert res.success  # structured refusal, NOT a crash
    rec = _record(res.output)
    assert rec["status"] == "refused"
    assert rec["reason"] == "duplicate_label"


async def test_capacity_cap_surfaces_structured_refusal() -> None:
    sessions = SessionRegistry()
    for i in range(MAX_LIVE_SESSIONS):
        sessions.spawn(f"s{i}", "scout")
    services = StepServices(session_registry=sessions, owl_registry=_registry_with_specialist())
    token = set_services(services)
    trace = TraceContext.start("s", trace_id="tr", channel="cli", owl_name="secretary")
    try:
        res = await SessionsSpawnTool().execute(label="extra", owl="scout")
    finally:
        TraceContext.reset(trace)
        reset_services(token)
    rec = _record(res.output)
    assert rec["status"] == "refused"
    assert rec["reason"] == "too_many_sessions"


async def test_no_registry_wired_degrades_structured() -> None:
    services = StepServices(session_registry=None, owl_registry=_registry_with_specialist())
    token = set_services(services)
    trace = TraceContext.start("s", trace_id="tr", channel="cli", owl_name="secretary")
    try:
        res = await SessionsSpawnTool().execute(label="x", owl="scout")
    finally:
        TraceContext.reset(trace)
        reset_services(token)
    rec = _record(res.output)
    assert rec["status"] == "refused"
    assert rec["reason"] == "unavailable"


async def test_unresolved_owl_refuses() -> None:
    # No owl_registry → resolver returns None → structured refusal.
    sessions = SessionRegistry()
    services = StepServices(session_registry=sessions, owl_registry=None)
    token = set_services(services)
    trace = TraceContext.start("s", trace_id="tr", channel="cli", owl_name="secretary")
    try:
        res = await SessionsSpawnTool().execute(label="x", owl="nobody")
    finally:
        TraceContext.reset(trace)
        reset_services(token)
    rec = _record(res.output)
    assert rec["status"] == "refused"
    assert rec["reason"] == "unresolved_owl"


async def test_invalid_args_hard_failure() -> None:
    res = await SessionsSpawnTool().execute(label=123)  # type: ignore[arg-type]
    assert res.success is False
    assert "invalid arguments" in (res.error or "")


def test_depth_gate_cross_check_sessions_spawn_excluded_for_children() -> None:
    # S0 cross-check: a delegated child (depth>0) must never see/run sessions_spawn.
    assert "sessions_spawn" in _CHILD_EXCLUDED_TOOLS


def test_manifest_severity_and_group() -> None:
    m = SessionsSpawnTool().manifest
    assert m.action_severity == "write"
    assert m.toolset_group == "agents"
