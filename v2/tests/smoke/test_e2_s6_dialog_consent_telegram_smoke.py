"""E2-S6 SMOKE — browser_dialog consent round-trip, Telegram → live browser → end.

The canonical consequential-tool smoke: a real inbound Telegram message drives the
GENUINE path (adapter → scanner → AsyncioBackend pipeline → execute._dispatch →
ConsequentialActionGate → ConsentPolicy → TelegramConsentPrompter → inline
keyboard), the user "taps" Approve through the REAL CallbackRouter, and only THEN
does browser_dialog accept the JS dialog on a LIVE Camoufox page (unblocking the
page's confirm()). Audit rows are asserted along the trace.

A real confirm() blocks the page's JS thread (so aria_snapshot/evaluate would hang
while it is pending) — the dialog is therefore triggered asynchronously and
resolved by dialog_id; the snapshot-surfacing of pending dialogs is unit-tested
separately (tests/tools/browser/test_dialog.py). Skipped if Camoufox can't launch.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from stackowl.audit.logger import AuditLogger
from stackowl.channels.telegram.adapter import TelegramChannelAdapter
from stackowl.channels.telegram.callbacks import CallbackRouter
from stackowl.channels.telegram.consent import TelegramConsentPrompter
from stackowl.channels.telegram.settings import TelegramSettings
from stackowl.config.browser import BrowserSettings
from stackowl.config.test_mode import TestModeGuard
from stackowl.db.pool import DbPool
from stackowl.gateway.scanner import GatewayScanner
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import StreamRegistry
from stackowl.tools.browser.runtime import CamoufoxRuntime
from stackowl.tools.browser.sessions import BrowserSessionRegistry
from stackowl.tools.consent import ConsentPolicy, RoutingPrompter
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

USER_ID = 717171


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


class _DialogProvider:
    """Tool-loop provider that accepts a pending dialog by id (consequential)."""

    protocol = "anthropic"

    def __init__(self, session_id: str, page_handle: str, dialog_id: str) -> None:
        self._args = {"session_id": session_id, "page_handle": page_handle,
                      "action": "accept", "dialog_id": dialog_id}
        self.result = ""

    async def complete_with_tools(self, *, user_text, system_text, tool_schemas, tool_dispatcher, history=None):  # noqa: ANN001
        self.result = await tool_dispatcher("browser_dialog", dict(self._args))
        return ("handled dialog", [{"name": "browser_dialog", "args": {}, "result": self.result}])

    async def complete(self, *a, **k):  # pragma: no cover
        return ""

    async def stream(self, *a, **k):  # pragma: no cover
        if False:
            yield ""


class _FakeProviderRegistry:
    def __init__(self, provider: _DialogProvider) -> None:
        self._p = provider

    def get(self, name: str) -> _DialogProvider:
        return self._p

    def get_by_tier(self, tier: str) -> _DialogProvider:
        return self._p


@dataclass
class _Env:
    adapter: TelegramChannelAdapter
    bot: _FakeBot
    scanner: GatewayScanner
    backend: AsyncioBackend
    stream_registry: StreamRegistry
    callback_router: CallbackRouter
    audit: AuditLogger
    provider: _DialogProvider


@pytest.fixture(autouse=True)
def _live_io():  # noqa: ANN202
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]
    yield
    TestModeGuard._active = prev  # type: ignore[attr-defined]


def _browser_settings(tmp_path: Path) -> BrowserSettings:
    return BrowserSettings(
        headless_mode="true",
        max_concurrent_sessions=2,
        max_concurrent_pages_per_session=2,
        session_idle_timeout_minutes=30,
        dialog_auto_dismiss_seconds=30.0,
        profiles_dir=tmp_path / "profiles",
        screenshots_dir=tmp_path / "shots",
        downloads_dir=tmp_path / "dl",
        browser_cache_dir=tmp_path / "cache",
    )


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
            update = SimpleNamespace(callback_query=SimpleNamespace(id=f"cb-{len(env.bot.answered)}", data=cd))
            await env.callback_router.route(update, None)
            return
        await asyncio.sleep(0.02)
    raise AssertionError("consent prompt never appeared on Telegram")


async def _turn(env: _Env, text: str, *, tap: str) -> str:
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
    before = len(env.bot.messages)
    run_task = asyncio.create_task(env.backend.run(state))
    out_task = asyncio.create_task(env.adapter.send(reader))
    await _tap(env, tap)
    await run_task
    await out_task
    env.stream_registry.remove(msg.trace_id)
    return "".join(m["text"] for m in env.bot.messages[before:] if m["reply_markup"] is None)


@pytest.mark.integration
async def test_smoke_dialog_accept_via_telegram_consent(tmp_db: DbPool, tmp_path: Path) -> None:
    settings = _browser_settings(tmp_path)
    runtime = CamoufoxRuntime(settings)
    sessions = BrowserSessionRegistry(runtime, settings)
    try:
        # Live page; trigger a confirm() asynchronously so it becomes a *pending*
        # dialog (a blocking confirm would otherwise hang evaluate/snapshot).
        try:
            sid = await sessions.open("local")
            sess, page, handle = await sessions.get_page(sid)
            await page.goto("data:text/html,<h1>dlg</h1>", wait_until="domcontentloaded", timeout=30_000)
            await page.evaluate(
                "() => { setTimeout(() => { window.__confirmed = confirm('Proceed?'); }, 0); }"
            )
            # Wait for page.on("dialog") to capture it.
            for _ in range(100):
                if sess.observers.get(handle) and sess.observers[handle].dialogs:
                    break
                await asyncio.sleep(0.05)
        except Exception as exc:
            pytest.skip(f"live Camoufox unavailable: {type(exc).__name__}: {exc}")

        obs = sess.observers.get(handle)
        if not obs or not obs.dialogs:
            pytest.skip("dialog was not captured by this engine build")
        dialog_id = next(iter(obs.dialogs))

        # --- consent + Telegram wiring (real components) ---
        adapter = TelegramChannelAdapter(TelegramSettings(allowed_user_ids=frozenset({USER_ID})))
        bot = _FakeBot()
        adapter._bot_app = _FakeBotApp(bot)
        adapter._bot_user_id = 999
        adapter._bot_username = ""

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
        router = CallbackRouter(tmp_db, adapter)
        await router.ensure_table()
        router.register("consent:", prompter.handle_callback)
        adapter.attach_callback_router(router)

        provider = _DialogProvider(sid, handle, dialog_id)
        services = StepServices(
            provider_registry=_FakeProviderRegistry(provider),  # type: ignore[arg-type]
            tool_registry=ToolRegistry.with_defaults(),
            consent_gate=gate,
            stream_registry=StreamRegistry(),
            db_pool=tmp_db,
            browser_runtime=runtime,
            browser_sessions=sessions,
        )
        env = _Env(
            adapter=adapter, bot=bot, scanner=GatewayScanner(owl_registry=None),
            backend=AsyncioBackend(services=services), stream_registry=services.stream_registry,  # type: ignore[arg-type]
            callback_router=router, audit=audit, provider=provider,
        )

        out = await _turn(env, "confirm the popup", tap="once")

        # 1) consent keyboard reached the user's Telegram chat (consequential gate fired).
        kb = [m for m in bot.messages if m["reply_markup"] is not None]
        assert kb and kb[0]["chat_id"] == USER_ID
        # 2) the tool ran and accepted the dialog (page JS unblocked → __confirmed=true).
        assert '"ok": true' in provider.result.lower(), provider.result
        confirmed = await page.evaluate("() => window.__confirmed")
        assert confirmed is True, "confirm() did not resolve true after gated accept"
        assert dialog_id not in obs.dialogs  # resolved + popped
        # 3) audit recorded an allow decision for browser_dialog.
        decisions = [r for r in audit.tail(50) if r["event_type"] == "consent.decision"]
        assert any(
            d["target"] == "browser_dialog" and json.loads(d["details"])["decision"] == "allow"
            for d in decisions
        ), decisions
        assert out.strip()
    finally:
        await sessions.close_all()
        await runtime.stop()
