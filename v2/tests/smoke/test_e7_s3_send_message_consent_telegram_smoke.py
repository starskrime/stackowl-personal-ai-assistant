"""E7-S3 SMOKE — send_message: REAL consent round-trip → REAL ProactiveDeliverer.

``send_message`` is CONSEQUENTIAL, so its per-story smoke must exercise the GENUINE
consent round-trip — NOT a stubbed confirm_fn — and then transport the body through
the REAL S0 chokepoint.

A real inbound Telegram update traverses the GENUINE path (adapter → scanner →
AsyncioBackend pipeline → execute._dispatch → ConsequentialActionGate →
ConsentPolicy → RoutingPrompter → TelegramConsentPrompter → inline keyboard).
The smoke "taps" YES/NO through the REAL CallbackRouter (the same machinery the
skill_manage consent smoke uses). Only on YES does ``send_message`` run, building a
``Notification(channel_name="telegram")`` and handing it to the REAL
``ProactiveDeliverer``, which asks the REAL ``NotificationRouter`` for a decision
(writing the REAL ``notification_log`` row in the migrated SQLite db) and, on
``delivered``, resolves the SAME ``TelegramChannelAdapter`` off the REAL
``ChannelRegistry`` singleton and ``send_text``s the body to the ``_FakeBot``.

REAL: the consent gate + policy + Telegram prompter + CallbackRouter, the
ProactiveDeliverer, the NotificationRouter, the ChannelRegistry singleton, the
migrated DbPool, the pipeline, the ToolRegistry/SendMessageTool, and the Telegram
adapter's send_text transport.
FAKED: the provider (scripted tool calls instead of an LLM) and the Telegram bot
HTTP transport (``_FakeBot`` captures outbound text/keyboards in-process).
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from stackowl.audit.logger import AuditLogger
from stackowl.channels.registry import ChannelRegistry
from stackowl.channels.telegram.adapter import TelegramChannelAdapter
from stackowl.channels.telegram.callbacks import CallbackRouter
from stackowl.channels.telegram.consent import TelegramConsentPrompter
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
from stackowl.tools.consent import ConsentPolicy, RoutingPrompter
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

USER_ID = 757575
SEND_TEXT = "Deploy finished ✅"


def _settings() -> Settings:
    # quiet_hours disabled by default → in_quiet_hours() is False regardless of
    # clock; default_channel "telegram" so an omitted channel still routes here.
    return cast(
        Settings,
        SimpleNamespace(notifications=NotificationSettings(default_channel="telegram")),
    )


class _FakeBot:
    def __init__(self) -> None:
        self.messages: list[dict] = []
        self.answered: list[str] = []

    async def send_message(self, chat_id, text, parse_mode=None, reply_markup=None):  # noqa: ANN001
        self.messages.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup})

    async def answer_callback_query(self, callback_id, text=None):  # noqa: ANN001
        self.answered.append(callback_id)


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

    async def complete_with_tools(self, *, user_text, system_text, tool_schemas, tool_dispatcher):  # noqa: ANN001
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
    callback_router: CallbackRouter
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


def _cd_for(markup, scope: str) -> str:  # noqa: ANN001
    for row in markup.inline_keyboard:
        for btn in row:
            if btn.callback_data.endswith(f":{scope}"):
                return btn.callback_data
    raise AssertionError(f"no {scope} button in consent keyboard")


async def _tap(env: _Env, scope: str) -> None:
    for _ in range(250):
        kb = [m for m in env.bot.messages if m["reply_markup"] is not None]
        if kb:
            cd = _cd_for(kb[-1]["reply_markup"], scope)
            update = SimpleNamespace(
                callback_query=SimpleNamespace(id=f"cb-{len(env.bot.answered)}", data=cd)
            )
            await env.callback_router.route(update, None)
            return
        await asyncio.sleep(0.02)
    raise AssertionError("consent prompt never appeared on Telegram")


async def _turn(env: _Env, text: str, *, tap: str) -> None:
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
    await _tap(env, tap)
    await run_task
    await out_task
    env.stream_registry.remove(msg.session_id)


async def _build(tmp_db: DbPool, tmp_path: Path) -> _Env:
    settings = _settings()

    # --- the SAME adapter drives inbound (the user's message + the consent prompt
    # + the YES/NO tap) AND outbound (the deliverer's send_text). Registering it on
    # the singleton is what makes the REAL ProactiveDeliverer's registry.get(
    # "telegram") resolve to it.
    adapter = TelegramChannelAdapter(TelegramSettings(allowed_user_ids=frozenset({USER_ID})))
    bot = _FakeBot()
    adapter._bot_app = _FakeBotApp(bot)
    adapter._bot_user_id = 999
    adapter._bot_username = ""
    assert adapter.channel_name == "telegram"
    ChannelRegistry.instance().register(adapter)

    # --- REAL consent chokepoint: ConsentPolicy → RoutingPrompter →
    # TelegramConsentPrompter (inline keyboard), resolved by the REAL CallbackRouter
    # when the user "taps". Audit-backed (real AuditLogger over a tmp sqlite).
    audit_path = tmp_path / "audit.db"
    conn = sqlite3.connect(audit_path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS audit_log (audit_id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "event_type TEXT NOT NULL, actor TEXT, target TEXT, timestamp REAL NOT NULL, "
        "details TEXT NOT NULL, integrity_hash TEXT NOT NULL)"
    )
    conn.commit()
    conn.close()
    audit = AuditLogger(audit_path)
    routing = RoutingPrompter()
    prompter = TelegramConsentPrompter(adapter, timeout_seconds=5.0)
    routing.register("telegram", prompter)
    gate = ConsequentialActionGate(ConsentPolicy(prompter=routing, audit_logger=audit))
    router_cb = CallbackRouter(tmp_db, adapter)
    await router_cb.ensure_table()
    router_cb.register("consent:", prompter.handle_callback)
    adapter.attach_callback_router(router_cb)

    # --- REAL S0 transport chokepoint: real NotificationRouter (writes
    # notification_log) + real ProactiveDeliverer (resolves the adapter off the real
    # registry and transports). Clock pinned to noon UTC — well outside any window
    # (and quiet_hours is disabled anyway) so the router decides "delivered".
    notif_router = NotificationRouter(
        db=tmp_db, settings=settings,
        clock=lambda: datetime(2026, 5, 30, 12, 0, tzinfo=UTC),
    )
    assert notif_router.get_focus_mode() == "off"
    deliverer = ProactiveDeliverer(
        router=notif_router, registry=ChannelRegistry.instance(), settings=settings
    )

    provider = _ScriptedProvider()
    services = StepServices(
        provider_registry=_FakeProviderRegistry(provider),  # type: ignore[arg-type]
        tool_registry=ToolRegistry.with_defaults(),
        consent_gate=gate,  # REAL Telegram-backed consent gate
        stream_registry=StreamRegistry(),
        proactive_deliverer=deliverer,  # REAL deliverer on the chokepoint
        db_pool=tmp_db,  # REAL migrated DbPool — router writes notification_log here
    )
    return _Env(
        adapter=adapter, bot=bot, scanner=GatewayScanner(owl_registry=None),
        backend=AsyncioBackend(services=services), stream_registry=services.stream_registry,  # type: ignore[arg-type]
        callback_router=router_cb, provider=provider,
    )


async def test_smoke_send_message_consent_yes_delivers(tmp_db: DbPool, tmp_path: Path) -> None:
    env = await _build(tmp_db, tmp_path)

    # As the user: a message that drives a send_message turn (the agent decides to
    # push a proactive "deploy finished" notice).
    env.provider.script.append(
        ("send_message", {"action": "send", "text": SEND_TEXT, "target": "telegram"})
    )
    await _turn(env, "let me know when the deploy is done", tap="once")

    # 1) The consent prompt reached Telegram with an inline keyboard, to the user.
    kb = [m for m in env.bot.messages if m["reply_markup"] is not None]
    assert kb, [m["text"] for m in env.bot.messages]
    assert kb[0]["chat_id"] == USER_ID, kb[0]
    assert _cd_for(kb[-1]["reply_markup"], "once"), "no YES button on consent keyboard"

    # 2) YES tap let the tool proceed → it reports the REAL deliverer's outcome.
    record = json.loads(env.provider.results[0])["record"]
    assert record["action"] == "send", env.provider.results[0]
    assert record["delivery_status"] == "delivered", env.provider.results[0]
    assert record["urgency"] == "normal", record  # hard-clamped

    # 3) PROOF the REAL ProactiveDeliverer transported the body to the wire: the
    # send body is in the fake bot, to the user's chat (and is NOT the consent
    # prompt — that one carried a keyboard; the delivered body has none).
    body_msgs = [m for m in env.bot.messages if m["text"] == SEND_TEXT]
    assert body_msgs, [m["text"] for m in env.bot.messages]
    assert body_msgs[0]["chat_id"] == USER_ID, body_msgs[0]
    assert body_msgs[0]["reply_markup"] is None, body_msgs[0]

    # 4) The REAL router wrote a 'delivered' audit row to the REAL notification_log.
    log_rows = await tmp_db.fetch_all(
        "SELECT urgency, channel, delivery_status FROM notification_log", ()
    )
    assert len(log_rows) == 1, log_rows
    assert log_rows[0]["delivery_status"] == "delivered", log_rows[0]
    assert log_rows[0]["channel"] == "telegram", log_rows[0]
    assert log_rows[0]["urgency"] == "normal", log_rows[0]


async def test_smoke_send_message_consent_no_blocks_delivery(
    tmp_db: DbPool, tmp_path: Path
) -> None:
    env = await _build(tmp_db, tmp_path)

    env.provider.script.append(
        ("send_message", {"action": "send", "text": SEND_TEXT, "target": "telegram"})
    )
    # Same flow, but the user taps NO on the inline keyboard.
    await _turn(env, "let me know when the deploy is done", tap="deny")

    # The consent prompt still reached Telegram (the gate fired)...
    kb = [m for m in env.bot.messages if m["reply_markup"] is not None]
    assert kb, [m["text"] for m in env.bot.messages]

    # ...but NO tap blocks the tool: send_message was never run, so the dispatch
    # returns the gate's "declined / not granted" sentinel — NOT a send record.
    assert "was not run" in env.provider.results[0], env.provider.results[0]

    # The body NEVER hit the wire (the only keyboard-less message, if any, is not
    # the send body).
    assert not [m for m in env.bot.messages if m["text"] == SEND_TEXT], [
        m["text"] for m in env.bot.messages
    ]

    # The REAL deliverer was never invoked → no notification_log row at all.
    log_rows = await tmp_db.fetch_all("SELECT delivery_status FROM notification_log", ())
    assert log_rows == [], log_rows
