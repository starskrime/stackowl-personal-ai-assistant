"""E4-S5/S6 SMOKE — session_search + transcripts AS THE USER, Telegram → end.

A real inbound Telegram message traverses the GENUINE path (adapter → scanner →
AsyncioBackend → execute._dispatch → ToolRegistry → session_search / transcripts).
Messages are seeded into the SAME session_id the pipeline assigns (captured mid-
flow) so the visibility guard's own-session path is exercised; a seeded secret
proves redaction is applied on the way back out. Read tools — no consent.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from stackowl.channels.telegram.adapter import TelegramChannelAdapter
from stackowl.channels.telegram.settings import TelegramSettings
from stackowl.config.test_mode import TestModeGuard
from stackowl.db.pool import DbPool
from stackowl.gateway.scanner import GatewayScanner
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import StreamRegistry
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

USER_ID = 888888


async def _seed(db: DbPool, *, session_id: str, owl_name: str, turns: list[tuple[str, str]]) -> None:
    conv = uuid.uuid4().hex
    base = datetime(2026, 1, 1, tzinfo=UTC)
    await db.execute(
        "INSERT INTO conversations (id, session_id, owl_name, started_at, message_count) VALUES (?,?,?,?,?)",
        (conv, session_id, owl_name, base.isoformat(), len(turns)),
    )
    for i, (role, content) in enumerate(turns):
        await db.execute(
            "INSERT INTO messages (id, conversation_id, role, content, created_at) VALUES (?,?,?,?,?)",
            (uuid.uuid4().hex, conv, role, content, (base + timedelta(seconds=i)).isoformat()),
        )


class _FakeBot:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_message(self, chat_id, text, parse_mode=None, reply_markup=None):  # noqa: ANN001
        self.messages.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup})

    async def answer_callback_query(self, callback_id, text=None):  # noqa: ANN001
        pass


class _FakeBotApp:
    def __init__(self, bot: _FakeBot) -> None:
        self.bot = bot

    def add_handler(self, handler: object) -> None:
        pass


class _ScriptedProvider:
    protocol = "anthropic"

    def __init__(self) -> None:
        self.script: list[tuple[str, dict]] = []
        self.results: list[str] = []

    async def complete_with_tools(self, *, user_text, system_text, tool_schemas, tool_dispatcher, history=None):  # noqa: ANN001
        name, args = self.script.pop(0)
        out = await tool_dispatcher(name, args)
        self.results.append(out)
        return (out, [{"name": name, "args": args, "result": out}])

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


@pytest.fixture(autouse=True)
def _live_io():  # noqa: ANN202
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]
    yield
    TestModeGuard._active = prev  # type: ignore[attr-defined]


async def test_smoke_session_tools_through_telegram(tmp_db: DbPool) -> None:
    adapter = TelegramChannelAdapter(TelegramSettings(allowed_user_ids=frozenset({USER_ID})))
    bot = _FakeBot()
    adapter._bot_app = _FakeBotApp(bot)
    adapter._bot_user_id = 999
    adapter._bot_username = ""
    provider = _ScriptedProvider()
    services = StepServices(
        provider_registry=_FakeProviderRegistry(provider),  # type: ignore[arg-type]
        tool_registry=ToolRegistry.with_defaults(),
        consent_gate=ConsequentialActionGate(),
        stream_registry=StreamRegistry(),
        db_pool=tmp_db,
    )
    env = _Env(
        adapter=adapter, bot=bot, scanner=GatewayScanner(owl_registry=None),
        backend=AsyncioBackend(services=services), stream_registry=services.stream_registry,  # type: ignore[arg-type]
        provider=provider,
    )

    # Intake the inbound message to learn the session_id the pipeline will use,
    # then seed prior turns (incl. a secret) into THAT session before running.
    update = SimpleNamespace(
        effective_message=SimpleNamespace(text="what did we say earlier about the token"),
        effective_user=SimpleNamespace(id=USER_ID),
        effective_chat=SimpleNamespace(id=USER_ID),
    )
    await env.adapter._handle_update(update, None)
    msg = await env.adapter.receive()
    decision = env.scanner.scan(msg)
    await _seed(
        tmp_db, session_id=msg.session_id, owl_name=decision.target,
        turns=[
            ("user", "deploy with token sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"),
            ("assistant", "noted, deploying"),
        ],
    )

    _writer, reader = env.stream_registry.create(msg.trace_id)
    state = PipelineState(
        trace_id=msg.trace_id, session_id=msg.session_id, input_text=msg.text,
        channel=msg.channel, owl_name=decision.target, pipeline_step="start",
    )
    # transcripts (own current session) returns the ordered log with the secret REDACTED.
    provider.script.append(("transcripts", {}))
    run_task = asyncio.create_task(env.backend.run(state))
    out_task = asyncio.create_task(env.adapter.send(reader))
    await run_task
    await out_task
    env.stream_registry.remove(msg.trace_id)

    out = provider.results[0]
    assert "deploying" in out  # the ordered transcript came back through the pipeline
    assert "sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ012345" not in out  # secret REDACTED on the way out
    assert bot.messages and bot.messages[-1]["chat_id"] == USER_ID
