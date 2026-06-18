"""E8-S4 SMOKE — sessions_send driven AS THE USER, Telegram input → REAL pipeline.

A real inbound Telegram update traverses the GENUINE path (TelegramChannelAdapter →
GatewayScanner → AsyncioBackend pipeline → execute._dispatch → ToolRegistry →
Sessions{Spawn,Send}Tool → the REAL ``SessionRegistry`` → a REAL nested continue-run
pipeline for the session's owl → the reply travels back over the real stream to the
user over Telegram).

The user first SPAWNS a session (E8-S3), then SENDS to it (E8-S4). The send is a
CONTINUE-RUN: the tool looks the session up by label and runs its owl ONCE with the
persisted history + the new message under the REAL delegation_governor, persisting
the grown history. A SECOND send proves continuity (the first turn is in context).
An unknown-label send proves the structured refusal surfaces to the user.

REAL: the DbPool (tmp_db, fully migrated), the whole pipeline, the ToolRegistry +
Sessions{Spawn,Send}Tool, the ``SessionRegistry`` (sessions actually created and
continued), the ``A2AQueue``, the ConcurrencyGovernor, and the OwlRegistry
(secretary + scout). FAKED: ONLY the provider (scripted — secretary drives the tool
calls, scout answers the continue-run) and the Telegram bot transport (captures
outbound text in-process). The SessionRegistry + the nested continue-run are NOT
stubbed.

This FAILS if the tool is unwired (not registered / no registry on services): the
send never reaches the REAL registry, the continue-run never runs, and the reply
never reaches the user.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from stackowl.channels.telegram.adapter import TelegramChannelAdapter
from stackowl.channels.telegram.settings import TelegramSettings
from stackowl.config.test_mode import TestModeGuard
from stackowl.db.pool import DbPool
from stackowl.gateway.scanner import GatewayScanner
from stackowl.memory.sqlite_bridge import SqliteMemoryBridge
from stackowl.messaging.a2a import A2AQueue
from stackowl.owls.concurrency import ConcurrencyGovernor
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.owls.session_registry import SessionRegistry
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import StreamRegistry
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

USER_ID = 818181


class _FakeBot:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_message(self, chat_id, text, parse_mode=None, reply_markup=None):  # noqa: ANN001
        self.messages.append({"chat_id": chat_id, "text": text})

    async def answer_callback_query(self, callback_id, text=None):  # noqa: ANN001
        pass


class _FakeBotApp:
    def __init__(self, bot: _FakeBot) -> None:
        self.bot = bot

    def add_handler(self, handler: object) -> None:
        pass


class _ScriptedProvider:
    """One provider serving BOTH owls.

    * As the SECRETARY (top-level user turn) it parses a 2-token command from the
      user text — ``"spawn <label>"`` / ``"send <label> <msg...>"`` — and drives the
      matching real tool through the dispatcher, weaving the structured record into
      the final answer so it reaches the user.
    * As the SCOUT (the nested continue-run, NO tools in its turn) it answers the
      session message and reports how many prior history turns it saw — so the test
      can prove continuity from the delivered text.
    """

    protocol = "anthropic"

    def __init__(self) -> None:
        self.tool_outputs: list[str] = []

    async def complete_with_tools(
        self, *, user_text, system_text, tool_schemas,
        tool_dispatcher, history=None, **_kwargs,
    ):  # noqa: ANN001
        names = {_name(s) for s in tool_schemas}
        # The scout's continue-run sees no spawn/send tools (depth>0 exclusion) —
        # it is the session owl answering, so just reply with the seen-history count.
        if "sessions_send" not in names and "sessions_spawn" not in names:
            prior = list(history or [])
            return (f"scout answer to {user_text!r} (history={len(prior)})", [])

        parts = user_text.strip().split(maxsplit=2)
        verb = parts[0] if parts else ""
        label = parts[1] if len(parts) > 1 else ""
        if verb == "spawn":
            out = await tool_dispatcher("sessions_spawn", {"label": label, "owl": "scout"})
            self.tool_outputs.append(out)
            rec = json.loads(out).get("record", {})
            final = f"spawn '{label}': {rec.get('status', '?')}."
            return (final, [{"name": "sessions_spawn", "args": {"label": label}, "result": out}])
        # verb == "send"
        msg = parts[2] if len(parts) > 2 else ""
        out = await tool_dispatcher("sessions_send", {"label": label, "message": msg})
        self.tool_outputs.append(out)
        rec = json.loads(out).get("record", {})
        status = rec.get("status", "?")
        reply = rec.get("reply", "")
        detail = rec.get("detail", "")
        final = f"send '{label}': {status}. reply={reply} {detail}".strip()
        return (final, [{"name": "sessions_send", "args": {"label": label}, "result": out}])

    async def complete(self, *a, **k):  # pragma: no cover
        return ""

    async def stream(self, *a, **k):  # pragma: no cover
        if False:
            yield ""


def _name(schema: dict) -> str:
    n = schema.get("name")
    if isinstance(n, str):
        return n
    fn = schema.get("function")
    return fn.get("name", "") if isinstance(fn, dict) else ""


class _FakeProviderRegistry:
    def __init__(self, p: _ScriptedProvider) -> None:
        self._p = p

    def get(self, name: str) -> _ScriptedProvider:
        return self._p

    def get_by_tier(self, tier: str) -> _ScriptedProvider:
        return self._p

    def get_with_cascade(self, tier: str) -> _ScriptedProvider:
        return self._p


@dataclass
class _Env:
    adapter: TelegramChannelAdapter
    bot: _FakeBot
    scanner: GatewayScanner
    backend: AsyncioBackend
    stream_registry: StreamRegistry
    provider: _ScriptedProvider
    sessions: SessionRegistry
    bridge: SqliteMemoryBridge


@pytest.fixture(autouse=True)
def _live_io():  # noqa: ANN202
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]
    yield
    TestModeGuard._active = prev  # type: ignore[attr-defined]


def _registry_with_scout() -> OwlRegistry:
    reg = OwlRegistry.with_default_secretary()
    reg.register(
        OwlAgentManifest(
            name="scout", role="research-scout",
            system_prompt="You research things.", model_tier="standard",
        )
    )
    return reg


def _build_env(tmp_db: DbPool) -> _Env:
    adapter = TelegramChannelAdapter(TelegramSettings(allowed_user_ids=frozenset({USER_ID})))
    bot = _FakeBot()
    adapter._bot_app = _FakeBotApp(bot)
    adapter._bot_user_id = 999
    adapter._bot_username = ""

    provider = _ScriptedProvider()
    a2a_queue = A2AQueue()
    sessions = SessionRegistry(a2a_queue=a2a_queue)
    bridge = SqliteMemoryBridge(db=tmp_db)
    services = StepServices(
        provider_registry=_FakeProviderRegistry(provider),  # type: ignore[arg-type]
        tool_registry=ToolRegistry.with_defaults(),
        consent_gate=ConsequentialActionGate(),
        stream_registry=StreamRegistry(),
        owl_registry=_registry_with_scout(),
        a2a_queue=a2a_queue,
        delegation_governor=ConcurrencyGovernor(),
        session_registry=sessions,  # REAL registry wired exactly as the orchestrator does
        memory_bridge=bridge,  # REAL bridge → classify reads + consolidate writes session turns
        db_pool=tmp_db,
    )
    return _Env(
        adapter=adapter, bot=bot, scanner=GatewayScanner(owl_registry=None),
        backend=AsyncioBackend(services=services), stream_registry=services.stream_registry,  # type: ignore[arg-type]
        provider=provider, sessions=sessions, bridge=bridge,
    )


async def _turn(env: _Env, text: str) -> None:
    update = SimpleNamespace(
        effective_message=SimpleNamespace(text=text),
        effective_user=SimpleNamespace(id=USER_ID),
        effective_chat=SimpleNamespace(id=USER_ID),
    )
    await env.adapter._handle_update(update, None)
    msg = await env.adapter.receive()
    decision = env.scanner.scan(msg)
    input_text = decision.stripped_text if decision.stripped_text is not None else msg.text
    _writer, reader = env.stream_registry.create(msg.trace_id)
    state = PipelineState(
        trace_id=msg.trace_id, session_id=msg.session_id, input_text=input_text,
        channel=msg.channel, owl_name=decision.target, pipeline_step="start",
    )
    run_task = asyncio.create_task(env.backend.run(state))
    out_task = asyncio.create_task(env.adapter.send(reader))
    await run_task
    await out_task
    env.stream_registry.remove(msg.trace_id)


def _delivered_last(env: _Env) -> str:
    msgs = [m["text"] for m in env.bot.messages if m["chat_id"] == USER_ID]
    return msgs[-1] if msgs else ""


async def test_smoke_spawn_then_send_continuity_THROUGH_THE_BRIDGE(tmp_db: DbPool) -> None:
    """The SECOND send must SEE the first turn — continuity flows through the REAL
    MemoryBridge (classify reads session:worker, consolidate writes it), NOT through
    any handle-state. This FAILS if classify/consolidate/session wiring is unwired
    (e.g. memory_bridge omitted → classify early-returns → history stays empty)."""
    env = _build_env(tmp_db)

    # 1) The user spawns a session 'worker' (E8-S3, real registry).
    await _turn(env, "spawn worker")
    assert env.sessions.get("worker") is not None, "spawn did not land in the REAL registry"

    # 2) First send: "remember X". The SCOUT continue-run saw EMPTY history (nothing
    # stored yet under session:worker), and the reply reaches the user.
    await _turn(env, "send worker remember-mango")
    send_rec = json.loads(env.provider.tool_outputs[-1])["record"]
    assert send_rec["status"] == "delivered", send_rec
    assert send_rec["owl"] == "scout", send_rec
    assert "scout answer" in send_rec["reply"], send_rec
    assert "history=0" in send_rec["reply"], send_rec  # nothing prior in the bridge yet
    assert "scout answer" in _delivered_last(env), _delivered_last(env)

    # The continue-run turn was PERSISTED to the BRIDGE under session:worker (by
    # consolidate, depth-1, no skip) — NOT on the handle (which has no history).
    handle = env.sessions.get("worker")
    assert handle is not None and not hasattr(handle, "history"), handle
    turns = await env.bridge.recent_conversation_turns(session_id="session:worker", limit=10)
    assert len(turns) == 1, f"first send's turn not persisted to the bridge: {turns}"
    assert "remember-mango" in turns[0].content, turns[0].content

    # 3) SECOND send: "what did I say?". classify reads the FIRST turn back from the
    # bridge under session:worker (1 stored turn → 2 messages), so the scout SEES it.
    # This is the continuity assertion — it fails if the bridge path is disconnected.
    await _turn(env, "send worker what-did-I-say")
    send_rec2 = json.loads(env.provider.tool_outputs[-1])["record"]
    assert "history=2" in send_rec2["reply"], send_rec2  # the scout saw the prior turn
    assert "scout answer" in _delivered_last(env), _delivered_last(env)
    # Both turns are now under the session id in the bridge.
    turns2 = await env.bridge.recent_conversation_turns(session_id="session:worker", limit=10)
    assert len(turns2) == 2, f"second send's turn not persisted: {turns2}"


async def test_smoke_unknown_session_refusal_surfaces_to_user(tmp_db: DbPool) -> None:
    env = _build_env(tmp_db)

    # Send to a label that was never spawned → structured refusal reaches the user.
    await _turn(env, "send ghost hello")
    rec = json.loads(env.provider.tool_outputs[-1])["record"]
    assert rec["status"] == "refused", rec
    assert rec["reason"] == "unknown_session", rec
    # No auto-spawn — the typo did not silently create a session.
    assert env.sessions.get("ghost") is None
    assert "refused" in _delivered_last(env), _delivered_last(env)
