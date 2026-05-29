"""E2-S1 SMOKE — browser_snapshot + click-by-ref driven AS THE USER, Telegram → end.

NOT a direct execute() call. A fake inbound Telegram update traverses the GENUINE
path: TelegramChannelAdapter._handle_update → GatewayScanner → AsyncioBackend (full
pipeline) → execute._dispatch → ToolRegistry → browser_snapshot / browser_click,
then the response is delivered back out through adapter.send. A fake bot transport
captures outbound (no network), but every StackOwl component on the path is real —
AND the browser substrate is a LIVE Camoufox/Firefox session, so this also proves
the aria-ref keystone end-to-end on the real engine:

    user message → pipeline → browser_snapshot (aria_snapshot mode="ai") → [ref=eN]
    → browser_click(ref=eN) via the aria-ref selector engine → page actuates.

The fixture page's button flips ``document.title`` to "CLICKED" on click, so a
title assertion after the turn proves the click fired through the whole stack.

Live browser ⇒ marked slow; skipped automatically if Camoufox can't launch here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from stackowl.channels.telegram.adapter import TelegramChannelAdapter
from stackowl.channels.telegram.settings import TelegramSettings
from stackowl.config.browser import BrowserSettings
from stackowl.config.test_mode import TestModeGuard
from stackowl.gateway.scanner import GatewayScanner
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import StreamRegistry
from stackowl.tools.browser.runtime import CamoufoxRuntime
from stackowl.tools.browser.sessions import BrowserSessionRegistry
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

USER_ID = 515151

# A minimal page: a button that sets document.title on click (no network).
_FIXTURE = (
    "data:text/html,"
    "<html><body><h1>Hi</h1>"
    "<p>static body text</p>"
    "<button onclick=\"document.title='CLICKED'\">Submit</button>"
    "</body></html>"
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


class _SnapshotThenClickProvider:
    """Tool-loop provider: snapshot the live page, parse a ref, click it by ref."""

    protocol = "anthropic"

    def __init__(self, session_id: str, page_handle: str) -> None:
        self._sid = session_id
        self._ph = page_handle
        self.calls: list[str] = []

    async def complete_with_tools(self, *, user_text, system_text, tool_schemas, tool_dispatcher):  # noqa: ANN001
        # Thread session_id + page_handle exactly as a real model would after
        # browser_navigate returned them (so the tools act on the live page).
        args = {"session_id": self._sid, "page_handle": self._ph}
        snap = await tool_dispatcher("browser_snapshot", dict(args))
        self.calls.append("browser_snapshot")
        m = re.search(r'button[^\n]*\[ref=([A-Za-z0-9]+)\]', snap) or re.search(r"\[ref=([A-Za-z0-9]+)\]", snap)
        clicked = "no-ref"
        if m:
            ref = m.group(1)
            await tool_dispatcher("browser_click", {**args, "ref": ref})
            self.calls.append(f"browser_click:{ref}")
            clicked = ref
        text = f"snapshotok clicked {clicked}"
        return (text, [{"name": "browser_click", "args": {"ref": clicked}, "result": text}])

    async def complete(self, *a, **k):  # pragma: no cover
        return ""

    async def stream(self, *a, **k):  # pragma: no cover
        if False:
            yield ""


class _FakeProviderRegistry:
    def __init__(self, provider: _SnapshotThenClickProvider) -> None:
        self._p = provider

    def get(self, name: str) -> _SnapshotThenClickProvider:
        return self._p

    def get_by_tier(self, tier: str) -> _SnapshotThenClickProvider:
        return self._p


@dataclass
class _Env:
    adapter: TelegramChannelAdapter
    bot: _FakeBot
    scanner: GatewayScanner
    backend: AsyncioBackend
    stream_registry: StreamRegistry
    provider: _SnapshotThenClickProvider
    page: object


@pytest.fixture(autouse=True)
def _live_io():  # noqa: ANN202
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]
    yield
    TestModeGuard._active = prev  # type: ignore[attr-defined]


def _settings(tmp_path: Path) -> BrowserSettings:
    return BrowserSettings(
        headless_mode="true",
        max_concurrent_sessions=2,
        max_concurrent_pages_per_session=2,
        session_idle_timeout_minutes=30,
        profiles_dir=tmp_path / "profiles",
        screenshots_dir=tmp_path / "shots",
        downloads_dir=tmp_path / "dl",
        browser_cache_dir=tmp_path / "cache",
    )


async def _turn(env: _Env, text: str) -> str:
    """One full inbound→outbound turn. Returns the delivered outbound text."""
    update = SimpleNamespace(
        effective_message=SimpleNamespace(text=text),
        effective_user=SimpleNamespace(id=USER_ID),
        effective_chat=SimpleNamespace(id=USER_ID),
    )
    await env.adapter._handle_update(update, None)  # real intake (auth + enqueue)
    msg = await env.adapter.receive()
    decision = env.scanner.scan(msg)
    input_text = decision.stripped_text if decision.stripped_text is not None else msg.text
    _writer, reader = env.stream_registry.create(msg.session_id)
    state = PipelineState(
        trace_id=msg.trace_id, session_id=msg.session_id, input_text=input_text,
        channel=msg.channel, owl_name=decision.target, pipeline_step="start",
    )
    before = len(env.bot.messages)
    import asyncio
    run_task = asyncio.create_task(env.backend.run(state))
    out_task = asyncio.create_task(env.adapter.send(reader))
    await run_task
    await out_task
    env.stream_registry.remove(msg.session_id)
    return "".join(m["text"] for m in env.bot.messages[before:] if m["reply_markup"] is None)


class _MultiToolProvider:
    """Tool-loop provider exercising get_images → press → back on the live page."""

    protocol = "anthropic"

    def __init__(self, session_id: str, page_handle: str) -> None:
        self._args = {"session_id": session_id, "page_handle": page_handle}
        self.results: dict[str, str] = {}

    async def complete_with_tools(self, *, user_text, system_text, tool_schemas, tool_dispatcher):  # noqa: ANN001
        self.results["get_images"] = await tool_dispatcher("browser_get_images", dict(self._args))
        self.results["press"] = await tool_dispatcher("browser_press", {**self._args, "key": "Tab"})
        self.results["back"] = await tool_dispatcher("browser_back", dict(self._args))
        text = "ran getimages press back"
        return (text, [{"name": "browser_back", "args": {}, "result": text}])

    async def complete(self, *a, **k):  # pragma: no cover
        return ""

    async def stream(self, *a, **k):  # pragma: no cover
        if False:
            yield ""


# Only console.* messages — NOT an uncaught throw. Any uncaught page error
# crashes this headless Camoufox build's driver connection (engine fragility,
# see FF-E2-4), so the live smoke exercises the messages bucket; the errors
# (pageerror) bucket is covered by the unit test's fire_error path.
_PAGE_CONSOLE = (
    b"<!DOCTYPE html><html><head><title>Console</title></head><body><script>"
    b"console.log('SMOKELOG'); console.error('SMOKEERRLINE');"
    b"</script></body></html>"
)
_PAGE_A = b"<html><head><title>PageA</title></head><body><h1>PageA</h1></body></html>"
_PAGE_B = (
    b"<html><head><title>PageB</title></head><body><h1>PageB</h1><input id='i'>"
    b"<img src='/p.png' alt='pic'>"
    b"<img src='data:image/png;base64,AAAA' alt='inline'></body></html>"
)


class _LocalServer:
    """Tiny localhost HTTP server serving /a and /b — real http:// navigations
    create genuine back-history (data: URLs do not on Firefox)."""

    def __init__(self) -> None:
        import http.server
        import threading

        pages = {"/a": _PAGE_A, "/b": _PAGE_B, "/c": _PAGE_CONSOLE}

        class _Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                body = pages.get(self.path, b"<html><body>ok</body></html>")
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a: object) -> None:  # silence
                pass

        self._srv = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
        self.port = self._srv.server_address[1]
        self._thread = threading.Thread(target=self._srv.serve_forever, daemon=True)

    def __enter__(self) -> _LocalServer:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._srv.shutdown()
        self._srv.server_close()


@pytest.mark.integration
async def test_smoke_back_press_getimages_through_telegram(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    runtime = CamoufoxRuntime(settings)
    sessions = BrowserSessionRegistry(runtime, settings)
    server = _LocalServer().__enter__()  # stays up for the whole turn
    try:
        base = f"http://127.0.0.1:{server.port}"
        try:
            sid = await sessions.open("local")
            _sess, page, handle = await sessions.get_page(sid)
            # Two real navigations → genuine back-history.
            await page.goto(f"{base}/a", wait_until="domcontentloaded", timeout=30_000)
            await page.goto(f"{base}/b", wait_until="domcontentloaded", timeout=30_000)
        except Exception as exc:
            pytest.skip(f"live Camoufox unavailable: {type(exc).__name__}: {exc}")

        provider = _MultiToolProvider(sid, handle)
        adapter = TelegramChannelAdapter(TelegramSettings(allowed_user_ids=frozenset({USER_ID})))
        bot = _FakeBot()
        adapter._bot_app = _FakeBotApp(bot)
        adapter._bot_user_id = 999
        adapter._bot_username = ""

        tools = ToolRegistry.with_defaults()
        services = StepServices(
            provider_registry=_FakeProviderRegistry(provider),  # type: ignore[arg-type]
            tool_registry=tools,
            consent_gate=ConsequentialActionGate(),
            stream_registry=StreamRegistry(),
            browser_runtime=runtime,
            browser_sessions=sessions,
        )
        env = _Env(
            adapter=adapter, bot=bot, scanner=GatewayScanner(owl_registry=None),
            backend=AsyncioBackend(services=services), stream_registry=services.stream_registry,  # type: ignore[arg-type]
            provider=provider, page=page,  # type: ignore[arg-type]
        )

        out = await _turn(env, "list images, press tab, then go back")

        # get_images: the served (non-data) image is listed; the data: URI is filtered out.
        assert "/p.png" in provider.results["get_images"]
        assert "base64" not in provider.results["get_images"]
        # press: dispatched ok.
        assert '"ok": true' in provider.results["press"].lower()
        # back: actually navigated to the previous history entry (PageA). FF-E2-1 is
        # FIXED by the browser.sessionhistory.max_entries baseline pref in
        # CamoufoxRuntime — Camoufox otherwise ships history disabled, making go_back
        # a no-op. This assertion proves the fix end-to-end on the live engine.
        assert '"navigated": true' in provider.results["back"], provider.results["back"]
        title_after = await page.title()  # type: ignore[attr-defined]
        assert "PageA" in title_after, f"browser_back did not return to PageA (title={title_after!r})"
        # response delivered back out to the user's Telegram chat.
        assert out.strip() and bot.messages[-1]["chat_id"] == USER_ID
    finally:
        await sessions.close_all()
        await runtime.stop()
        server.__exit__(None, None, None)


class _ConsoleProvider:
    """Tool-loop provider that reads the console buffer."""

    protocol = "anthropic"

    def __init__(self, session_id: str, page_handle: str) -> None:
        self._args = {"session_id": session_id, "page_handle": page_handle}
        self.result = ""

    async def complete_with_tools(self, *, user_text, system_text, tool_schemas, tool_dispatcher):  # noqa: ANN001
        self.result = await tool_dispatcher("browser_console", dict(self._args))
        return ("read console", [{"name": "browser_console", "args": {}, "result": self.result}])

    async def complete(self, *a, **k):  # pragma: no cover
        return ""

    async def stream(self, *a, **k):  # pragma: no cover
        if False:
            yield ""


@pytest.mark.integration
async def test_smoke_console_through_telegram(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    runtime = CamoufoxRuntime(settings)
    sessions = BrowserSessionRegistry(runtime, settings)
    server = _LocalServer().__enter__()
    try:
        try:
            sid = await sessions.open("local")
            _sess, page, handle = await sessions.get_page(sid)  # observers wired here
            # http-served page that logs + throws (a data: URL with an uncaught throw
            # crashes headless Camoufox; http navigation is robust).
            await page.goto(f"http://127.0.0.1:{server.port}/c", wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(300)  # let console/pageerror events settle
        except Exception as exc:
            pytest.skip(f"live Camoufox unavailable: {type(exc).__name__}: {exc}")

        provider = _ConsoleProvider(sid, handle)
        adapter = TelegramChannelAdapter(TelegramSettings(allowed_user_ids=frozenset({USER_ID})))
        bot = _FakeBot()
        adapter._bot_app = _FakeBotApp(bot)
        adapter._bot_user_id = 999
        adapter._bot_username = ""
        services = StepServices(
            provider_registry=_FakeProviderRegistry(provider),  # type: ignore[arg-type]
            tool_registry=ToolRegistry.with_defaults(),
            consent_gate=ConsequentialActionGate(),
            stream_registry=StreamRegistry(),
            browser_runtime=runtime,
            browser_sessions=sessions,
        )
        env = _Env(
            adapter=adapter, bot=bot, scanner=GatewayScanner(owl_registry=None),
            backend=AsyncioBackend(services=services), stream_registry=services.stream_registry,  # type: ignore[arg-type]
            provider=provider, page=page,  # type: ignore[arg-type]
        )

        out = await _turn(env, "show me the console")

        # The eagerly-wired buffer captured the load-time console messages (log +
        # console.error), proving the substrate fills from page birth through the
        # real pipeline. (errors/pageerror bucket is unit-tested — see FF-E2-4.)
        assert "SMOKELOG" in provider.result, provider.result
        assert "SMOKEERRLINE" in provider.result, provider.result
        assert '"error_count":' in provider.result  # structure present
        assert out.strip() and bot.messages[-1]["chat_id"] == USER_ID
    finally:
        await sessions.close_all()
        await runtime.stop()
        server.__exit__(None, None, None)


@pytest.mark.integration
async def test_smoke_snapshot_then_click_by_ref_through_telegram(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    runtime = CamoufoxRuntime(settings)
    sessions = BrowserSessionRegistry(runtime, settings)
    try:
        # Live session navigated to the fixture (this is the page the user is "on").
        try:
            sid = await sessions.open("local")
            _sess, page, handle = await sessions.get_page(sid)
            await page.goto(_FIXTURE, wait_until="domcontentloaded", timeout=30_000)
        except Exception as exc:  # Camoufox can't launch in this env → skip, don't fail
            pytest.skip(f"live Camoufox unavailable: {type(exc).__name__}: {exc}")

        provider = _SnapshotThenClickProvider(sid, handle)
        adapter = TelegramChannelAdapter(TelegramSettings(allowed_user_ids=frozenset({USER_ID})))
        bot = _FakeBot()
        adapter._bot_app = _FakeBotApp(bot)
        adapter._bot_user_id = 999
        adapter._bot_username = ""

        tools = ToolRegistry.with_defaults()  # real registry incl. browser_snapshot + browser_click
        services = StepServices(
            provider_registry=_FakeProviderRegistry(provider),  # type: ignore[arg-type]
            tool_registry=tools,
            consent_gate=ConsequentialActionGate(),  # snapshot/click(read/write) don't gate
            stream_registry=StreamRegistry(),
            browser_runtime=runtime,
            browser_sessions=sessions,
        )
        env = _Env(
            adapter=adapter, bot=bot, scanner=GatewayScanner(owl_registry=None),
            backend=AsyncioBackend(services=services), stream_registry=services.stream_registry,  # type: ignore[arg-type]
            provider=provider, page=page,
        )

        out = await _turn(env, "look at the page and click submit")

        # 1) The tool loop actually fired both tools through the real pipeline.
        assert provider.calls and provider.calls[0] == "browser_snapshot"
        assert any(c.startswith("browser_click:") for c in provider.calls), provider.calls
        # 2) click-by-ref ACTUATED the live page (title flipped via the button's onclick).
        title = await page.title()  # type: ignore[attr-defined]
        assert title == "CLICKED", f"click-by-ref did not actuate the live page (title={title!r})"
        # 3) A response was delivered back out to the user's Telegram chat.
        assert out.strip(), "no outbound message delivered to Telegram"
        assert bot.messages and bot.messages[-1]["chat_id"] == USER_ID
    finally:
        await sessions.close_all()
        await runtime.stop()
