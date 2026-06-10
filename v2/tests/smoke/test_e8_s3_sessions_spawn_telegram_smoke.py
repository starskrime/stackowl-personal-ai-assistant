"""E8-S3 SMOKE — sessions_spawn driven AS THE USER, Telegram input → REAL registry.

A real inbound Telegram update traverses the GENUINE path (TelegramChannelAdapter →
GatewayScanner → AsyncioBackend pipeline → execute._dispatch → ToolRegistry →
SessionsSpawnTool → a REAL ``SessionRegistry.spawn`` → the session is registered and
the confirmation travels back over the real stream to the user over Telegram).

The user turn (owl=secretary) emits a ``sessions_spawn`` tool call
``{"label": ..., "owl": "scout"}``. That call runs through the real pipeline and the
real SessionRegistry; the tool's structured record is woven into the secretary's
final answer, which the adapter delivers to the user.

REAL: the DbPool (tmp_db, fully migrated), the whole pipeline, the ToolRegistry +
SessionsSpawnTool, the ``SessionRegistry`` (sessions actually created), the
``A2AQueue``, and the OwlRegistry (secretary + scout). FAKED: ONLY the provider
(scripted) and the Telegram bot transport (captures outbound text in-process).
``SessionRegistry.spawn`` itself is NOT stubbed — the session is genuinely created.

This FAILS if the tool is unwired (no registry on services / not registered):
the spawn never lands in the REAL registry and the confirmation never reaches the
user.
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
from stackowl.owls.delegation_limits import MAX_LIVE_SESSIONS
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.owls.session_registry import SessionRegistry
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import StreamRegistry
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

USER_ID = 757575


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
    """Secretary-only scripted provider that emits a sessions_spawn tool call.

    The user's message text carries the label to spawn (so cap/dup turns can drive
    different labels). The provider calls sessions_spawn(label, owl=scout) through
    the REAL dispatcher, then returns a final answer that incorporates the tool's
    structured status so it reaches the user.
    """

    protocol = "anthropic"

    def __init__(self) -> None:
        self.tool_outputs: list[str] = []

    async def complete_with_tools(self, *, user_text, system_text, tool_schemas, tool_dispatcher, history=None):  # noqa: ANN001
        label = user_text.strip()
        out = await tool_dispatcher("sessions_spawn", {"label": label, "owl": "scout"})
        self.tool_outputs.append(out)
        rec = json.loads(out).get("record", {})
        status = rec.get("status", "?")
        detail = rec.get("detail", "")
        final = f"session '{label}': {status}. {detail}".strip()
        return (final, [{"name": "sessions_spawn", "args": {"label": label}, "result": out}])

    async def complete(self, *a, **k):  # pragma: no cover
        return ""

    async def stream(self, *a, **k):  # pragma: no cover
        if False:
            yield ""


class _FakeProviderRegistry:
    def __init__(self, p: _ScriptedProvider) -> None:
        self._p = p

    def get(self, name: str) -> _ScriptedProvider:
        return self._p

    def get_by_tier(self, tier: str) -> _ScriptedProvider:
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
    services = StepServices(
        provider_registry=_FakeProviderRegistry(provider),  # type: ignore[arg-type]
        tool_registry=ToolRegistry.with_defaults(),
        consent_gate=ConsequentialActionGate(),
        stream_registry=StreamRegistry(),
        owl_registry=_registry_with_scout(),
        a2a_queue=a2a_queue,
        delegation_governor=ConcurrencyGovernor(),
        session_registry=sessions,  # REAL registry wired exactly as the orchestrator does
        memory_bridge=SqliteMemoryBridge(db=tmp_db),  # REAL bridge → classify/consolidate run
        db_pool=tmp_db,
    )
    return _Env(
        adapter=adapter, bot=bot, scanner=GatewayScanner(owl_registry=None),
        backend=AsyncioBackend(services=services), stream_registry=services.stream_registry,  # type: ignore[arg-type]
        provider=provider, sessions=sessions,
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


def _delivered(env: _Env) -> str:
    return "\n".join(m["text"] for m in env.bot.messages if m["chat_id"] == USER_ID)


async def test_smoke_sessions_spawn_real_registry_through_telegram(tmp_db: DbPool) -> None:
    env = _build_env(tmp_db)

    # The user asks to spawn a session labelled 'researcher'.
    await _turn(env, "researcher")

    # (1) The tool actually ran via the REAL pipeline (reached through
    # execute._dispatch → ToolRegistry → SessionsSpawnTool, not a direct call).
    assert env.provider.tool_outputs, "secretary never reached sessions_spawn via the pipeline"
    record = json.loads(env.provider.tool_outputs[0])["record"]
    assert record["status"] == "spawned", record
    assert record["owl"] == "scout", record

    # (2) The session is REALLY in the REAL SessionRegistry, addressable by label.
    handle = env.sessions.get("researcher")
    assert handle is not None, "session not created in the real registry"
    assert handle.owl_name == "scout"
    assert [h.label for h in env.sessions.all()] == ["researcher"]

    # (3) The user got a confirmation over Telegram.
    delivered = _delivered(env)
    assert "researcher" in delivered, delivered
    assert "spawned" in delivered, delivered
    assert env.bot.messages[-1]["chat_id"] == USER_ID


async def test_smoke_duplicate_label_surfaces_to_user(tmp_db: DbPool) -> None:
    env = _build_env(tmp_db)
    # Pre-seed the label so the user's spawn collides.
    env.sessions.spawn("researcher", "scout")

    await _turn(env, "researcher")

    record = json.loads(env.provider.tool_outputs[0])["record"]
    assert record["status"] == "refused", record
    assert record["reason"] == "duplicate_label", record
    # Still exactly one session — no silent overwrite.
    assert len(env.sessions.all()) == 1
    # The refusal reached the user.
    assert "refused" in _delivered(env), _delivered(env)


async def test_smoke_capacity_cap_surfaces_to_user(tmp_db: DbPool) -> None:
    env = _build_env(tmp_db)
    for i in range(MAX_LIVE_SESSIONS):
        env.sessions.spawn(f"pre{i}", "scout")

    await _turn(env, "one-too-many")

    record = json.loads(env.provider.tool_outputs[0])["record"]
    assert record["status"] == "refused", record
    assert record["reason"] == "too_many_sessions", record
    # The cap held — the new label was NOT created.
    assert env.sessions.get("one-too-many") is None
    assert len(env.sessions.all()) == MAX_LIVE_SESSIONS
    assert "refused" in _delivered(env), _delivered(env)
