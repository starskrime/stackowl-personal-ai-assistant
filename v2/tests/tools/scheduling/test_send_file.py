"""Tests for SendFileTool — agent outbound file/media over the channel registry (E8).

Three layers, mirroring the ``send_message`` test suite:

1. UNIT — ``execute`` called directly (bypasses the registry consent gate, proven
   in the SMOKE layer). A fake ProactiveDeliverer records each
   ``deliver(Notification)`` so the tests assert the threaded ``file_path``, the
   caption (on ``message``), the target channel, and the clamped ``normal``
   urgency. Covers: file outside workspace / missing / too large / blank →
   structured error with NO deliver; happy path; flood cap; deliverer
   missing/failed/raises; no-target.
2. TRANSPORT — the Telegram adapter's ``send_file`` against a fake bot:
   ``.mp4`` → send_video, ``.pdf`` → send_document, ``.png`` → send_photo; a send
   failure is structured/logged by the deliverer (no raise).
3. GATEWAY SMOKE — a full REAL-consent round-trip (consequential gate → Telegram
   inline keyboard → YES tap) drives the production pipeline so that the model's
   ``send_file`` dispatch ends in the REAL ProactiveDeliverer uploading the
   workspace file via the adapter's send_file to the fake bot (send_document).
   FAILS if ``send_file`` is not registered / not surfaced.

Plus: ``send_file`` appears in the secretary's per-owl PRESENTED schema (base set).
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

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
from stackowl.infra.trace import TraceContext
from stackowl.notifications.deliverer import ProactiveDeliverer
from stackowl.notifications.router import (
    DeliveryStatus,
    Notification,
    NotificationRouter,
)
from stackowl.paths import StackowlHome
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import (
    StepServices,
    reset_services,
    set_services,
)
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import StreamRegistry
from stackowl.tools.consent import ConsentPolicy, RoutingPrompter
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry
from stackowl.tools.scheduling.send_file import SendFileTool

# asyncio_mode = "auto" (pyproject) handles async tests; no module-level mark so
# the sync schema/severity checks below don't trigger a spurious asyncio warning.

_TRACE = "trace-sf-1"


# --------------------------------------------------------------------------- #
# UNIT layer
# --------------------------------------------------------------------------- #


class _FakeDeliverer:
    """Records deliver() calls and returns a scripted DeliveryStatus."""

    def __init__(self, status: str = "delivered") -> None:
        self.status = status
        self.calls: list[Notification] = []

    async def deliver(self, notification: Notification) -> str:
        self.calls.append(notification)
        return self.status


class _FakeAdapter:
    """Minimal ChannelAdapter stand-in — only ``channel_name`` is read here."""

    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def channel_name(self) -> str:
        return self._name

    async def send_text(self, text: str) -> None:  # pragma: no cover
        pass


@pytest.fixture
def _workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point StackowlHome.workspace() at an isolated tmp dir for the unit tests."""
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setattr(StackowlHome, "workspace", classmethod(lambda cls: ws))
    return ws


@pytest.fixture(autouse=True)
def _channels() -> Any:
    """Register two fake channels in the registry singleton; reset after each test."""
    reg = ChannelRegistry.instance()
    reg.reset()
    reg.register(_FakeAdapter("telegram"))
    reg.register(_FakeAdapter("cli"))
    yield reg
    reg.reset()


def _decode(output: str) -> dict[str, Any]:
    return json.loads(output)["record"]


async def _run(
    tool: SendFileTool,
    *,
    deliverer: Any,
    channel: str | None = "telegram",
    session_id: str | None = "sess-sf",
    trace_id: str | None = _TRACE,
    **kwargs: Any,
) -> Any:
    services = StepServices(proactive_deliverer=deliverer)
    stoken = set_services(services)
    ttoken = TraceContext.start(
        session_id=session_id, trace_id=trace_id, interactive=True, channel=channel
    )
    try:
        return await tool.execute(**kwargs)
    finally:
        TraceContext.reset(ttoken)
        reset_services(stoken)


def _make_file(workspace: Path, name: str = "clip.mp4", size: int = 16) -> Path:
    p = workspace / name
    p.write_bytes(b"x" * size)
    return p


async def test_happy_path_delivers_file_path_and_caption(_workspace: Path) -> None:
    deliverer = _FakeDeliverer()
    f = _make_file(_workspace)
    result = await _run(
        SendFileTool(),
        deliverer=deliverer,
        file_path=str(f),
        caption="here is your clip",
        target="telegram",
    )
    assert result.success is True
    assert len(deliverer.calls) == 1
    sent = deliverer.calls[0]
    assert sent.file_path == str(f.resolve())  # threaded through
    assert sent.message == "here is your clip"  # caption rides on message
    assert sent.channel_name == "telegram"
    assert sent.category == "agent_file"
    assert sent.urgency == "normal"  # hard-clamped
    record = _decode(result.output)
    assert record["delivery_status"] == "delivered"
    assert record["file_path"] == str(f.resolve())


async def test_target_omitted_defaults_to_session_channel(_workspace: Path) -> None:
    deliverer = _FakeDeliverer()
    f = _make_file(_workspace)
    result = await _run(
        SendFileTool(), deliverer=deliverer, channel="telegram", file_path=str(f)
    )
    assert result.success is True
    assert deliverer.calls[0].channel_name == "telegram"


async def test_caption_optional_blank_message(_workspace: Path) -> None:
    deliverer = _FakeDeliverer()
    f = _make_file(_workspace)
    result = await _run(SendFileTool(), deliverer=deliverer, file_path=str(f), target="cli")
    assert result.success is True
    assert deliverer.calls[0].message == ""  # no caption → empty message


async def test_bare_relative_name_resolves_under_workspace_and_sends(
    _workspace: Path,
) -> None:
    """A bare filename resolves to <workspace>/<name> and is accepted (H2)."""
    deliverer = _FakeDeliverer()
    _make_file(_workspace, name="video.mp4")
    result = await _run(
        SendFileTool(), deliverer=deliverer, file_path="video.mp4", target="telegram"
    )
    assert result.success is True
    expected = str((_workspace / "video.mp4").resolve())
    assert deliverer.calls[0].file_path == expected  # resolved under workspace
    assert _decode(result.output)["file_path"] == expected


async def test_missing_bare_name_gives_instructive_not_found_error(
    _workspace: Path,
) -> None:
    """A missing bare name → the new 'produce it first' error, NOT 'outside workspace'."""
    deliverer = _FakeDeliverer()
    result = await _run(
        SendFileTool(), deliverer=deliverer, file_path="ghost.mp4", target="telegram"
    )
    assert result.success is False
    err = result.error or ""
    assert "not in workspace yet" in err
    assert "produce it first" in err
    assert "outside workspace" not in err  # boundary error must NOT fire for a bare name
    assert deliverer.calls == []


async def test_file_outside_workspace_structured_error_no_deliver(
    _workspace: Path, tmp_path: Path
) -> None:
    """An absolute path outside the workspace is rejected — no exfiltration."""
    outside = tmp_path / "secret.txt"
    outside.write_text("host secret")
    deliverer = _FakeDeliverer()
    result = await _run(
        SendFileTool(), deliverer=deliverer, file_path=str(outside), target="telegram"
    )
    assert result.success is False
    assert "outside workspace" in (result.error or "")
    assert deliverer.calls == []  # no deliver, no raise


async def test_path_traversal_outside_workspace_rejected(_workspace: Path) -> None:
    """A ``..`` traversal that escapes the workspace is rejected."""
    deliverer = _FakeDeliverer()
    escaping = str(_workspace / ".." / "etc-passwd")
    result = await _run(
        SendFileTool(), deliverer=deliverer, file_path=escaping, target="telegram"
    )
    assert result.success is False
    assert "outside workspace" in (result.error or "")
    assert deliverer.calls == []


async def test_file_missing_structured_error(_workspace: Path) -> None:
    deliverer = _FakeDeliverer()
    ghost = str(_workspace / "does-not-exist.mp4")
    result = await _run(
        SendFileTool(), deliverer=deliverer, file_path=ghost, target="telegram"
    )
    assert result.success is False
    assert "not in workspace yet" in (result.error or "")
    assert deliverer.calls == []


async def test_directory_is_not_a_regular_file(_workspace: Path) -> None:
    deliverer = _FakeDeliverer()
    subdir = _workspace / "adir"
    subdir.mkdir()
    result = await _run(
        SendFileTool(), deliverer=deliverer, file_path=str(subdir), target="telegram"
    )
    assert result.success is False
    assert "not a regular file" in (result.error or "")
    assert deliverer.calls == []


async def test_file_too_large_structured_error(_workspace: Path) -> None:
    deliverer = _FakeDeliverer()
    big = _make_file(_workspace, name="big.bin", size=200)
    tool = SendFileTool(max_bytes=100)  # 200-byte file exceeds a 100-byte cap
    result = await _run(tool, deliverer=deliverer, file_path=str(big), target="telegram")
    assert result.success is False
    assert "too large" in (result.error or "")
    assert deliverer.calls == []


async def test_blank_file_path_structured_error(_workspace: Path) -> None:
    deliverer = _FakeDeliverer()
    result = await _run(
        SendFileTool(), deliverer=deliverer, file_path="   ", target="telegram"
    )
    assert result.success is False
    assert "blank file_path" in (result.error or "")
    assert deliverer.calls == []


async def test_unknown_channel_structured_error_no_deliver(_workspace: Path) -> None:
    deliverer = _FakeDeliverer()
    f = _make_file(_workspace)
    result = await _run(
        SendFileTool(), deliverer=deliverer, file_path=str(f), target="discord"
    )
    assert result.success is False
    assert "unknown channel" in (result.error or "")
    assert deliverer.calls == []


async def test_no_target_no_session_channel_structured_error(_workspace: Path) -> None:
    deliverer = _FakeDeliverer()
    f = _make_file(_workspace)
    result = await _run(
        SendFileTool(), deliverer=deliverer, channel=None, file_path=str(f)
    )
    assert result.success is False
    assert "no target channel" in (result.error or "")
    assert deliverer.calls == []


async def test_flood_cap_rejects_over_limit(_workspace: Path) -> None:
    deliverer = _FakeDeliverer()
    f = _make_file(_workspace)
    tool = SendFileTool(flood_max=2, flood_window_seconds=60)
    ok1 = await _run(tool, deliverer=deliverer, file_path=str(f), target="cli")
    ok2 = await _run(tool, deliverer=deliverer, file_path=str(f), target="cli")
    rejected = await _run(tool, deliverer=deliverer, file_path=str(f), target="cli")
    assert ok1.success is True
    assert ok2.success is True
    assert rejected.success is False
    assert "rate limited" in (rejected.error or "")
    assert len(deliverer.calls) == 2


async def test_deliverer_none_structured_deferred_no_raise(_workspace: Path) -> None:
    f = _make_file(_workspace)
    result = await _run(
        SendFileTool(), deliverer=None, file_path=str(f), target="telegram"
    )
    assert result.success is True  # structured, not a raise
    assert _decode(result.output)["delivery_status"] == "deferred"


async def test_deliver_failed_structured_no_raise(_workspace: Path) -> None:
    deliverer = _FakeDeliverer(status="failed")
    f = _make_file(_workspace)
    result = await _run(
        SendFileTool(), deliverer=deliverer, file_path=str(f), target="telegram"
    )
    assert result.success is True
    assert _decode(result.output)["delivery_status"] == "failed"


async def test_deliver_raises_self_heals_to_deferred(_workspace: Path) -> None:
    class _Raiser:
        async def deliver(self, notification: Notification) -> str:
            raise RuntimeError("boom")

    f = _make_file(_workspace)
    result = await _run(
        SendFileTool(), deliverer=_Raiser(), file_path=str(f), target="telegram"
    )
    assert result.success is True  # never raises out of execute
    assert _decode(result.output)["delivery_status"] == "deferred"


async def test_extra_field_forbidden(_workspace: Path) -> None:
    deliverer = _FakeDeliverer()
    f = _make_file(_workspace)
    result = await _run(
        SendFileTool(),
        deliverer=deliverer,
        file_path=str(f),
        target="telegram",
        bogus="nope",
    )
    assert result.success is False
    assert deliverer.calls == []


def test_send_file_is_consequential() -> None:
    assert SendFileTool().manifest.action_severity == "consequential"


# --------------------------------------------------------------------------- #
# TRANSPORT layer — the deliverer routes a file Notification to adapter.send_file,
# and the Telegram adapter picks the Bot API media sender by extension.
# --------------------------------------------------------------------------- #


class _FakeBot:
    """Records which media sender was called for the file send."""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.video: list[dict[str, Any]] = []
        self.photo: list[dict[str, Any]] = []
        self.document: list[dict[str, Any]] = []

    async def send_video(self, **kwargs: Any) -> None:
        if self.fail:
            raise RuntimeError("upload failed")
        self.video.append(kwargs)

    async def send_photo(self, **kwargs: Any) -> None:
        if self.fail:
            raise RuntimeError("upload failed")
        self.photo.append(kwargs)

    async def send_document(self, **kwargs: Any) -> None:
        if self.fail:
            raise RuntimeError("upload failed")
        self.document.append(kwargs)


class _FakeBotApp:
    def __init__(self, bot: _FakeBot) -> None:
        self.bot = bot


def _adapter_with_bot(bot: _FakeBot, chat_id: int = 4242) -> TelegramChannelAdapter:
    adapter = TelegramChannelAdapter(TelegramSettings(allowed_user_ids=frozenset({chat_id})))
    adapter._bot_app = _FakeBotApp(bot)  # type: ignore[assignment]
    adapter._last_chat_id = chat_id
    return adapter


@pytest.fixture
def _live_io() -> Any:
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]
    yield
    TestModeGuard._active = prev  # type: ignore[attr-defined]


async def test_adapter_mp4_uses_send_video(
    tmp_path: Path, _live_io: Any
) -> None:
    bot = _FakeBot()
    adapter = _adapter_with_bot(bot)
    f = tmp_path / "movie.mp4"
    f.write_bytes(b"v")
    await adapter.send_file(str(f), caption="watch this")
    assert len(bot.video) == 1
    assert bot.video[0]["chat_id"] == 4242
    assert bot.video[0]["caption"] == "watch this"
    assert bot.photo == [] and bot.document == []


async def test_adapter_pdf_uses_send_document(tmp_path: Path, _live_io: Any) -> None:
    bot = _FakeBot()
    adapter = _adapter_with_bot(bot)
    f = tmp_path / "report.pdf"
    f.write_bytes(b"d")
    await adapter.send_file(str(f))
    assert len(bot.document) == 1
    assert "caption" not in bot.document[0]  # no caption → key omitted
    assert bot.video == [] and bot.photo == []


async def test_adapter_png_uses_send_photo(tmp_path: Path, _live_io: Any) -> None:
    bot = _FakeBot()
    adapter = _adapter_with_bot(bot)
    f = tmp_path / "shot.png"
    f.write_bytes(b"p")
    await adapter.send_file(str(f), caption="a picture")
    assert len(bot.photo) == 1
    assert bot.video == [] and bot.document == []


async def test_adapter_no_extension_uses_send_document(
    tmp_path: Path, _live_io: Any
) -> None:
    bot = _FakeBot()
    adapter = _adapter_with_bot(bot)
    f = tmp_path / "noext"
    f.write_bytes(b"x")
    await adapter.send_file(str(f))
    assert len(bot.document) == 1


async def test_adapter_no_chat_is_noop(tmp_path: Path, _live_io: Any) -> None:
    bot = _FakeBot()
    adapter = _adapter_with_bot(bot)
    adapter._last_chat_id = None  # no active chat
    f = tmp_path / "movie.mp4"
    f.write_bytes(b"v")
    await adapter.send_file(str(f))  # logged no-op, never raises
    assert bot.video == [] and bot.document == [] and bot.photo == []


def _settings(default_channel: str = "telegram") -> Settings:
    return cast(
        Settings,
        SimpleNamespace(notifications=NotificationSettings(default_channel=default_channel)),
    )


class _StubRouter:
    def __init__(self, decision: DeliveryStatus) -> None:
        self._decision = decision

    async def deliver(self, notification: Notification) -> DeliveryStatus:
        return self._decision


async def test_deliverer_routes_file_to_adapter_send_file(
    tmp_path: Path, _live_io: Any
) -> None:
    """A delivered Notification with file_path lands on adapter.send_file (video)."""
    ChannelRegistry.instance().reset()
    bot = _FakeBot()
    adapter = _adapter_with_bot(bot)
    ChannelRegistry.instance().register(adapter)
    deliverer = ProactiveDeliverer(
        router=cast(NotificationRouter, _StubRouter("delivered")),
        registry=ChannelRegistry.instance(),
        settings=_settings(),
    )
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"v")
    note = Notification(
        message="caption here", urgency="normal", category="agent_file",
        channel_name="telegram", file_path=str(f),
    )
    status = await deliverer.deliver(note)
    assert status == "delivered"
    assert len(bot.video) == 1
    assert bot.video[0]["caption"] == "caption here"
    ChannelRegistry.instance().reset()


async def test_deliverer_file_send_failure_is_structured_no_raise(
    tmp_path: Path, _live_io: Any
) -> None:
    """A failing upload is mapped to ``failed`` by the deliverer — never raises."""
    ChannelRegistry.instance().reset()
    bot = _FakeBot(fail=True)
    adapter = _adapter_with_bot(bot)
    ChannelRegistry.instance().register(adapter)
    deliverer = ProactiveDeliverer(
        router=cast(NotificationRouter, _StubRouter("delivered")),
        registry=ChannelRegistry.instance(),
        settings=_settings(),
    )
    f = tmp_path / "report.pdf"
    f.write_bytes(b"d")
    note = Notification(
        message="", urgency="normal", category="agent_file",
        channel_name="telegram", file_path=str(f),
    )
    status = await deliverer.deliver(note)
    assert status == "failed"  # structured, no raise
    ChannelRegistry.instance().reset()


# --------------------------------------------------------------------------- #
# PRESENTED schema — send_file is in every owl's non-evictable base set.
# --------------------------------------------------------------------------- #


def test_send_file_in_presented_schema() -> None:
    """send_file surfaces to every owl via the guaranteed base set (empty profile)."""
    registry = ToolRegistry.with_defaults()
    schemas = registry.to_provider_schema("openai", profile=[], pins=[], hydrated=set())
    names = {s["function"]["name"] for s in schemas}  # type: ignore[index]
    assert "send_file" in names, (
        f"send_file not in the per-owl presented base set: {sorted(names)}"
    )


# --------------------------------------------------------------------------- #
# GATEWAY SMOKE — REAL consent round-trip → REAL ProactiveDeliverer → adapter
# send_file uploads the workspace file. FAILS if send_file isn't registered.
# Mirrors tests/smoke/test_e7_s3_send_message_consent_telegram_smoke.py.
# --------------------------------------------------------------------------- #

_USER_ID = 818181


class _SmokeBot:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.answered: list[str] = []
        self.documents: list[dict[str, Any]] = []

    async def send_message(self, chat_id, text, parse_mode=None, reply_markup=None):  # noqa: ANN001
        self.messages.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup})

    async def answer_callback_query(self, callback_id, text=None):  # noqa: ANN001
        self.answered.append(callback_id)

    async def send_document(self, **kwargs: Any) -> None:
        self.documents.append(kwargs)


class _SmokeBotApp:
    def __init__(self, bot: _SmokeBot) -> None:
        self.bot = bot

    def add_handler(self, handler: object) -> None:
        pass


class _ScriptedProvider:
    protocol = "anthropic"

    def __init__(self) -> None:
        self.script: list[tuple[str, dict[str, Any]]] = []
        self.results: list[str] = []

    async def complete_with_tools(
        self, *, user_text, system_text, tool_schemas,
        tool_dispatcher, history=None, **_kwargs,
    ):  # noqa: ANN001
        name, args = self.script.pop(0)
        out = await tool_dispatcher(name, args)
        self.results.append(out)
        return (out, [{"name": name, "args": args, "result": out}])

    async def complete(self, *a: Any, **k: Any) -> str:  # pragma: no cover
        return ""

    async def stream(self, *a: Any, **k: Any):  # pragma: no cover
        if False:
            yield ""


class _SmokeProviderRegistry:
    def __init__(self, p: _ScriptedProvider) -> None:
        self._p = p

    def get(self, name: str) -> _ScriptedProvider:
        return self._p

    def get_by_tier(self, tier: str) -> _ScriptedProvider:
        return self._p


@dataclass
class _Env:
    adapter: TelegramChannelAdapter
    bot: _SmokeBot
    scanner: GatewayScanner
    backend: AsyncioBackend
    stream_registry: StreamRegistry
    callback_router: CallbackRouter
    provider: _ScriptedProvider


def _cd_for(markup: Any, scope: str) -> str:
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
        effective_user=SimpleNamespace(id=_USER_ID),
        effective_chat=SimpleNamespace(id=_USER_ID),
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
    await _tap(env, tap)
    await run_task
    await out_task
    env.stream_registry.remove(msg.trace_id)


async def _build_smoke(tmp_db: DbPool, tmp_path: Path) -> _Env:
    settings = _settings()
    adapter = TelegramChannelAdapter(TelegramSettings(allowed_user_ids=frozenset({_USER_ID})))
    bot = _SmokeBot()
    adapter._bot_app = _SmokeBotApp(bot)  # type: ignore[assignment]
    adapter._bot_user_id = 999
    adapter._bot_username = ""
    ChannelRegistry.instance().register(adapter)

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

    notif_router = NotificationRouter(
        db=tmp_db, settings=settings,
        clock=lambda: datetime(2026, 5, 30, 12, 0, tzinfo=UTC),
    )
    deliverer = ProactiveDeliverer(
        router=notif_router, registry=ChannelRegistry.instance(), settings=settings
    )

    provider = _ScriptedProvider()
    services = StepServices(
        provider_registry=_SmokeProviderRegistry(provider),  # type: ignore[arg-type]
        tool_registry=ToolRegistry.with_defaults(),
        consent_gate=gate,
        stream_registry=StreamRegistry(),
        proactive_deliverer=deliverer,
        db_pool=tmp_db,
    )
    return _Env(
        adapter=adapter, bot=bot, scanner=GatewayScanner(owl_registry=None),
        backend=AsyncioBackend(services=services), stream_registry=services.stream_registry,  # type: ignore[arg-type]
        callback_router=router_cb, provider=provider,
    )


async def test_smoke_send_file_consent_yes_uploads_document(
    tmp_db: DbPool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]
    ChannelRegistry.instance().reset()
    # Workspace-scope the file the model will send.
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setattr(StackowlHome, "workspace", classmethod(lambda cls: ws))
    sent_file = ws / "result.pdf"
    sent_file.write_bytes(b"PDF-bytes")
    try:
        env = await _build_smoke(tmp_db, tmp_path)
        env.provider.script.append(
            ("send_file", {"file_path": str(sent_file), "caption": "your report", "target": "telegram"})
        )
        await _turn(env, "send me the report file", tap="session")

        # 1) consent prompt reached Telegram with an inline keyboard, to the user.
        kb = [m for m in env.bot.messages if m["reply_markup"] is not None]
        assert kb, [m["text"] for m in env.bot.messages]
        assert kb[0]["chat_id"] == _USER_ID

        # 2) YES tap → tool ran → REAL deliverer reported delivered.
        record = json.loads(env.provider.results[0])["record"]
        assert record["action"] == "send_file", env.provider.results[0]
        assert record["delivery_status"] == "delivered", env.provider.results[0]
        assert record["urgency"] == "normal"

        # 3) PROOF: the REAL ProactiveDeliverer uploaded the file via the adapter's
        # send_file → the fake bot recorded a send_document to the user's chat.
        assert len(env.bot.documents) == 1, env.bot.documents
        assert env.bot.documents[0]["chat_id"] == _USER_ID
        assert env.bot.documents[0]["caption"] == "your report"

        # 4) REAL router wrote a 'delivered' notification_log row.
        rows = await tmp_db.fetch_all(
            "SELECT channel, delivery_status FROM notification_log", ()
        )
        assert len(rows) == 1 and rows[0]["delivery_status"] == "delivered"
        assert rows[0]["channel"] == "telegram"
    finally:
        ChannelRegistry.instance().reset()
        TestModeGuard._active = prev  # type: ignore[attr-defined]
