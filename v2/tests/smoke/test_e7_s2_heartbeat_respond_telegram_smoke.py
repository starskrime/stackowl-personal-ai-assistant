"""E7-S2 SMOKE — heartbeat_respond → REAL ProactiveDeliverer → Telegram, AS THE USER.

This smoke closes the gap S0's own smoke deferred: it wires the GENUINE S0
transport chain end-to-end and proves a heartbeat-originated notification really
lands on the wire.

A real inbound Telegram update traverses the GENUINE path (adapter → scanner →
AsyncioBackend pipeline → execute._dispatch → ToolRegistry → HeartbeatRespondTool).
The tool builds a ``Notification(channel_name="telegram")`` and hands it to the
REAL ``ProactiveDeliverer``, which asks the REAL ``NotificationRouter`` for a
decision (writing the REAL ``notification_log`` row in the migrated SQLite db) and,
on ``delivered``, resolves the SAME ``TelegramChannelAdapter`` off the REAL
``ChannelRegistry`` singleton and calls ``send_text`` — pushing the heartbeat body
to the ``_FakeBot``.

REAL: the DbPool (tmp_db, fully migrated), the pipeline, the tool, the
ProactiveDeliverer, the NotificationRouter, the ChannelRegistry, and the Telegram
adapter's ``send_text`` transport method.
FAKED (per the E4/E7-S1 template): the provider (scripted tool calls instead of an
LLM) and the Telegram bot transport (``_FakeBot`` captures outbound text in-process).
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast

import pytest

from stackowl.channels.registry import ChannelRegistry
from stackowl.channels.telegram.adapter import TelegramChannelAdapter
from stackowl.channels.telegram.settings import TelegramSettings
from stackowl.config.notification_settings import NotificationSettings
from stackowl.config.settings import Settings
from stackowl.config.test_mode import TestModeGuard
from stackowl.db.pool import DbPool
from stackowl.gateway.scanner import GatewayScanner
from stackowl.notifications.deliverer import ProactiveDeliverer
from stackowl.notifications.router import NotificationRouter
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import StreamRegistry
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

USER_ID = 858585
HEARTBEAT_TEXT = "Heads up: CI is red on main."


def _settings() -> Settings:
    # quiet_hours disabled by default → in_quiet_hours() is False regardless of
    # clock; default_channel "telegram" so an omitted channel still routes here.
    return cast(
        Settings,
        SimpleNamespace(
            notifications=NotificationSettings(default_channel="telegram")
        ),
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


@pytest.fixture(autouse=True)
def _clean_registry():  # noqa: ANN202
    ChannelRegistry.instance().reset()
    yield
    ChannelRegistry.instance().reset()


async def _turn(env: _Env, text: str) -> str:
    update = SimpleNamespace(
        effective_message=SimpleNamespace(text=text),
        effective_user=SimpleNamespace(id=USER_ID),
        effective_chat=SimpleNamespace(id=USER_ID),
    )
    await env.adapter._handle_update(update, None)
    msg = await env.adapter.receive()
    decision = env.scanner.scan(msg)
    input_text = decision.stripped_text if decision.stripped_text is not None else msg.text
    _writer, reader = env.stream_registry.create(msg.session_id)
    state = PipelineState(
        trace_id=msg.trace_id, session_id=msg.session_id, input_text=input_text,
        channel=msg.channel, owl_name=decision.target, pipeline_step="start",
    )
    run_task = asyncio.create_task(env.backend.run(state))
    out_task = asyncio.create_task(env.adapter.send(reader))
    await run_task
    await out_task
    env.stream_registry.remove(msg.session_id)
    return ""


async def test_smoke_heartbeat_respond_delivers_through_real_deliverer(tmp_db: DbPool) -> None:
    settings = _settings()

    # --- the SAME adapter drives both inbound (the user's message) and outbound
    # (the deliverer's send_text). Registering it on the singleton is what makes
    # the REAL ProactiveDeliverer's registry.get("telegram") resolve to it.
    adapter = TelegramChannelAdapter(TelegramSettings(allowed_user_ids=frozenset({USER_ID})))
    bot = _FakeBot()
    adapter._bot_app = _FakeBotApp(bot)
    adapter._bot_user_id = 999
    adapter._bot_username = ""
    assert adapter.channel_name == "telegram"
    ChannelRegistry.instance().register(adapter)

    # --- the REAL S0 chokepoint: real router (writes notification_log) + real
    # deliverer (resolves the adapter off the real registry and transports).
    # Clock pinned to noon UTC — well outside any window (and quiet_hours is
    # disabled anyway) so the router decides "delivered", not "batched".
    router = NotificationRouter(
        db=tmp_db, settings=settings,
        clock=lambda: datetime(2026, 5, 30, 12, 0, tzinfo=UTC),
    )
    assert router.get_focus_mode() == "off"
    deliverer = ProactiveDeliverer(
        router=router, registry=ChannelRegistry.instance(), settings=settings
    )

    provider = _ScriptedProvider()
    services = StepServices(
        provider_registry=_FakeProviderRegistry(provider),  # type: ignore[arg-type]
        tool_registry=ToolRegistry.with_defaults(),
        consent_gate=ConsequentialActionGate(),
        stream_registry=StreamRegistry(),
        proactive_deliverer=deliverer,  # REAL deliverer on the chokepoint
        db_pool=tmp_db,  # REAL migrated DbPool — router writes notification_log here
    )
    env = _Env(
        adapter=adapter, bot=bot, scanner=GatewayScanner(owl_registry=None),
        backend=AsyncioBackend(services=services), stream_registry=services.stream_registry,  # type: ignore[arg-type]
        provider=provider,
    )

    # Turn 1: as the user, send a message that triggers a heartbeat_respond turn
    # concluding CI is red and asking to notify.
    provider.script.append((
        "heartbeat_respond",
        {
            "outcome": "ci_red",
            "notify": True,
            "summary": "CI failed on main",
            "notification_text": HEARTBEAT_TEXT,
        },
    ))
    await _turn(env, "anything I should know about the build?")

    record = json.loads(provider.results[0])["record"]
    # The tool reports the REAL deliverer's transport outcome.
    assert record["delivery_status"] == "delivered", provider.results[0]

    # PROOF the REAL ProactiveDeliverer transported the body through the registry
    # adapter to the wire: the heartbeat text is in the fake bot, to the user's chat.
    heartbeat_msgs = [m for m in bot.messages if m["text"] == HEARTBEAT_TEXT]
    assert heartbeat_msgs, [m["text"] for m in bot.messages]
    assert heartbeat_msgs[0]["chat_id"] == USER_ID, heartbeat_msgs[0]

    # The REAL router wrote a 'delivered' audit row to the REAL notification_log.
    log_rows = await tmp_db.fetch_all(
        "SELECT urgency, category, channel, delivery_status FROM notification_log", ()
    )
    assert len(log_rows) == 1, log_rows
    assert log_rows[0]["delivery_status"] == "delivered", log_rows[0]
    assert log_rows[0]["channel"] == "telegram", log_rows[0]
    assert log_rows[0]["urgency"] == "normal", log_rows[0]

    # Turn 2: HARD GATE end-to-end. The agent asks for priority="critical"; the
    # S0 clamp must neutralise it to 'normal'. Fresh trace → once-per-turn guard
    # does not block. We prove the clamp through the REAL notification_log row.
    provider.script.append((
        "heartbeat_respond",
        {
            "outcome": "ci_red_again",
            "notify": True,
            "summary": "still red",
            "notification_text": "Still red on main.",
            "priority": "critical",
        },
    ))
    await _turn(env, "and now?")

    record2 = json.loads(provider.results[1])["record"]
    assert record2["delivery_status"] == "delivered", provider.results[1]
    # The clamped urgency surfaces in the tool's own record too.
    assert record2["priority"] == "normal", record2

    # The hard gate, end-to-end: a SECOND delivered row exists, and its urgency is
    # 'normal' even though the agent requested 'critical' — the clamp held through
    # the whole real chain (tool → deliverer → router → notification_log).
    log_rows2 = await tmp_db.fetch_all(
        "SELECT urgency, delivery_status FROM notification_log "
        "ORDER BY created_at, rowid", ()
    )
    assert len(log_rows2) == 2, log_rows2
    assert log_rows2[1]["delivery_status"] == "delivered", log_rows2[1]
    assert log_rows2[1]["urgency"] == "normal", log_rows2[1]  # clamped, not 'critical'

    # And the second heartbeat body really hit the wire too.
    assert any(m["text"] == "Still red on main." for m in bot.messages), [
        m["text"] for m in bot.messages
    ]
