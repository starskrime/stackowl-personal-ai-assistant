"""Tests for SessionsSendTool (E8-S4) — continue-run, continuity, self-healing.

Network-free: the tool calls ``.execute()`` directly (bypassing __call__ /
TestModeGuard). A REAL SessionRegistry is injected via StepServices; a scripted
provider drives the continue-run pipeline (so no model network). Continuity is now
BRIDGE-BACKED: tests that exercise it wire a REAL ``SqliteMemoryBridge`` (so
``classify`` reads prior turns under ``session:{label}`` and ``consolidate`` writes
each turn back) — there is no handle.history. TraceContext is set via
TraceContext.start(...) so the tool reads trace/owl/channel. The S0 depth gate is
cross-checked against the execute step's _CHILD_EXCLUDED_TOOLS.
"""

from __future__ import annotations

import json

from stackowl.db.pool import DbPool
from stackowl.infra.trace import TraceContext
from stackowl.memory.sqlite_bridge import SqliteMemoryBridge
from stackowl.owls.concurrency import ConcurrencyGovernor
from stackowl.owls.registry import OwlRegistry
from stackowl.owls.session_registry import SessionRegistry
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.steps.execute import _CHILD_EXCLUDED_TOOLS
from stackowl.tools.agents.sessions_send import (
    _SEND_MAX_PER_WINDOW,
    SessionsSendTool,
)
from stackowl.tools.registry import ToolRegistry


def _registry_with_scout() -> OwlRegistry:
    reg = OwlRegistry.with_default_secretary()
    from stackowl.owls.manifest import OwlAgentManifest

    reg.register(
        OwlAgentManifest(
            name="scout", role="research-scout",
            system_prompt="You research.", model_tier="standard",
        )
    )
    return reg


class _ScriptedProvider:
    """Echoes the message and surfaces the prior-history length so a test can
    prove the continue-run actually threaded the session's persisted history."""

    protocol = "anthropic"

    def __init__(self) -> None:
        self.seen_history_lengths: list[int] = []

    async def complete_with_tools(self, *, user_text, system_text, tool_schemas, tool_dispatcher, history=None):  # noqa: ANN001, ANN204
        prior = list(history or [])
        self.seen_history_lengths.append(len(prior))
        return (f"reply to {user_text!r} (saw {len(prior)} prior turns)", [])

    async def complete(self, *a, **k):  # pragma: no cover
        return ""

    async def stream(self, *a, **k):  # pragma: no cover
        if False:
            yield ""


class _FailingProvider:
    protocol = "anthropic"

    async def complete_with_tools(self, *a, **k):  # noqa: ANN002, ANN003
        raise RuntimeError("provider boom")

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


def _services(
    sessions: SessionRegistry,
    provider: object,
    bridge: SqliteMemoryBridge | None = None,
) -> StepServices:
    return StepServices(
        provider_registry=_ProviderRegistry(provider),  # type: ignore[arg-type]
        tool_registry=ToolRegistry.with_defaults(),
        session_registry=sessions,
        owl_registry=_registry_with_scout(),
        memory_bridge=bridge,
        delegation_governor=ConcurrencyGovernor(),
    )


def _record(res_output: str) -> dict[str, object]:
    return json.loads(res_output)["record"]


async def _send(services: StepServices, **kwargs: object):  # noqa: ANN202
    token = set_services(services)
    trace = TraceContext.start("s", trace_id="tr", channel="cli", owl_name="secretary")
    try:
        return await SessionsSendTool().execute(**kwargs)
    finally:
        TraceContext.reset(trace)
        reset_services(token)


async def test_send_returns_reply_and_persists_turn_to_bridge(tmp_db: DbPool) -> None:
    bridge = SqliteMemoryBridge(db=tmp_db)
    sessions = SessionRegistry()
    sessions.spawn("worker", "scout")
    provider = _ScriptedProvider()
    res = await _send(_services(sessions, provider, bridge), label="worker", message="hello")

    assert res.success
    rec = _record(res.output)
    assert rec["status"] == "delivered"
    assert rec["owl"] == "scout"
    assert "hello" in str(rec["reply"])
    # No handle.history — the turn was persisted to the BRIDGE under session:worker
    # (consolidate's job). One staged conversation turn now exists for the session.
    handle = sessions.get("worker")
    assert handle is not None
    assert not hasattr(handle, "history")
    turns = await bridge.recent_conversation_turns(session_id="session:worker", limit=10)
    assert len(turns) == 1
    assert "hello" in turns[0].content


async def test_second_send_sees_first_turn_continuity_through_bridge(tmp_db: DbPool) -> None:
    bridge = SqliteMemoryBridge(db=tmp_db)
    sessions = SessionRegistry()
    sessions.spawn("worker", "scout")
    provider = _ScriptedProvider()
    services = _services(sessions, provider, bridge)

    await _send(services, label="worker", message="first")
    res2 = await _send(services, label="worker", message="second")

    # The second continue-run's classify read the first turn (1 stored turn → 2
    # messages) from the BRIDGE under session:worker — continuity, NOT handle-state.
    assert provider.seen_history_lengths == [0, 2]
    rec = _record(res2.output)
    assert "saw 2 prior turns" in str(rec["reply"])
    # Both turns persisted under the session id; the handle carries no history.
    turns = await bridge.recent_conversation_turns(session_id="session:worker", limit=10)
    assert len(turns) == 2


async def test_unknown_session_structured_refusal() -> None:
    sessions = SessionRegistry()
    res = await _send(_services(sessions, _ScriptedProvider()), label="nope", message="x")
    assert res.success  # structured refusal, NOT a crash
    rec = _record(res.output)
    assert rec["status"] == "refused"
    assert rec["reason"] == "unknown_session"
    # No auto-spawn — the typo did not silently create a session.
    assert sessions.get("nope") is None


async def test_run_failure_structured_and_session_kept() -> None:
    sessions = SessionRegistry()
    sessions.spawn("worker", "scout")
    res = await _send(_services(sessions, _FailingProvider()), label="worker", message="hi")
    assert res.success  # structured failure, NOT a raise
    rec = _record(res.output)
    assert rec["status"] == "error"
    # Failure is REPORTED, not masked as a fake reply (no-hidden-errors).
    assert "reply" not in rec
    # The session is PRESERVED — still addressable, no fake assistant turn stored.
    handle = sessions.get("worker")
    assert handle is not None
    assert handle.owl_name == "scout"


async def test_wait_false_sends_without_reply_text(tmp_db: DbPool) -> None:
    bridge = SqliteMemoryBridge(db=tmp_db)
    sessions = SessionRegistry()
    sessions.spawn("worker", "scout")
    res = await _send(
        _services(sessions, _ScriptedProvider(), bridge), label="worker", message="go", wait=False,
    )
    rec = _record(res.output)
    assert rec["status"] == "sent"
    assert "reply" not in rec
    # The run still happened (no async actor) — the turn persisted to the BRIDGE.
    turns = await bridge.recent_conversation_turns(session_id="session:worker", limit=10)
    assert len(turns) == 1


async def test_rate_limit_refuses_after_cap() -> None:
    sessions = SessionRegistry()
    sessions.spawn("worker", "scout")
    services = _services(sessions, _ScriptedProvider())
    tool = SessionsSendTool()  # one tool instance owns the per-session bucket
    token = set_services(services)
    trace = TraceContext.start("s", trace_id="tr", channel="cli", owl_name="secretary")
    try:
        records = []
        for _ in range(_SEND_MAX_PER_WINDOW + 1):
            res = await tool.execute(label="worker", message="ping")
            records.append(_record(res.output))
    finally:
        TraceContext.reset(trace)
        reset_services(token)
    # First _SEND_MAX_PER_WINDOW delivered; the next is a structured rate-limit
    # refusal (status='refused', reason='rate_limited').
    assert [r["status"] for r in records[:_SEND_MAX_PER_WINDOW]] == ["delivered"] * _SEND_MAX_PER_WINDOW
    assert records[_SEND_MAX_PER_WINDOW]["status"] == "refused"
    assert records[_SEND_MAX_PER_WINDOW]["reason"] == "rate_limited"


async def test_no_registry_wired_degrades_structured() -> None:
    services = StepServices(session_registry=None)
    res = await _send(services, label="x", message="y")
    rec = _record(res.output)
    assert rec["status"] == "refused"
    assert rec["reason"] == "unavailable"


async def test_invalid_args_hard_failure() -> None:
    res = await SessionsSendTool().execute(label=123)  # type: ignore[arg-type]
    assert res.success is False
    assert "invalid arguments" in (res.error or "")


async def test_no_spoof_origin_arg_rejected() -> None:
    # The tool server-stamps origin from TraceContext; a 'from' arg is extra and
    # forbidden (frozen extra='forbid') — a caller cannot spoof who sent.
    sessions = SessionRegistry()
    sessions.spawn("worker", "scout")
    res = await _send(
        _services(sessions, _ScriptedProvider()),
        label="worker", message="x", **{"from": "ceo-owl"},  # type: ignore[arg-type]
    )
    assert res.success is False
    assert "invalid arguments" in (res.error or "")


def test_depth_gate_cross_check_sessions_send_excluded_for_children() -> None:
    # S0 cross-check: a delegated child (depth>0) must never see/run sessions_send.
    assert "sessions_send" in _CHILD_EXCLUDED_TOOLS


def test_manifest_severity_and_group() -> None:
    m = SessionsSendTool().manifest
    assert m.action_severity == "write"
    assert m.toolset_group == "agents"
