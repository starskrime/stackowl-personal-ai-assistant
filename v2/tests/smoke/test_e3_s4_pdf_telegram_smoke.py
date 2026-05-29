"""E3-S4 SMOKE — pdf (Mode A → Mode B self-heal) driven AS THE USER, Telegram → end.

Uses a REAL pypdf-written PDF (blank page → no extractable text), so Mode A runs
genuine pypdf parsing (not a mock) and self-heals to Mode B, which routes the
document to a fake document-capable provider through the GENUINE pipeline
(adapter → scanner → AsyncioBackend → execute._dispatch → ToolRegistry → pdf).
Closes the "no real-PDF fixture" gap the unit tests leave. pdf is read-only.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from stackowl.channels.telegram.adapter import TelegramChannelAdapter
from stackowl.channels.telegram.settings import TelegramSettings
from stackowl.config.test_mode import TestModeGuard
from stackowl.gateway.scanner import GatewayScanner
from stackowl.paths import StackowlHome
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import StreamRegistry
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

USER_ID = 848484
_VISION_TEXT = "EXTRACTED-VIA-VISION-MODEL"


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


class _VisionProvider:
    """Drives the tool loop (calls pdf) AND is document-capable for Mode B."""

    protocol = "anthropic"
    supports_document = True
    name = "fake-vision"

    def __init__(self, pdf_path: str) -> None:
        self._pdf_path = pdf_path
        self.result = ""

    async def complete_with_tools(self, *, user_text, system_text, tool_schemas, tool_dispatcher):  # noqa: ANN001
        self.result = await tool_dispatcher("pdf", {"path": self._pdf_path})
        return (self.result, [{"name": "pdf", "args": {}, "result": self.result}])

    async def complete(self, messages, model=""):  # noqa: ANN001 — Mode B routing target
        from stackowl.providers.base import CompletionResult

        # The routed message should carry the PDF as a document block.
        assert messages and messages[0].documents, "Mode B must attach a document block"
        return CompletionResult(
            content=_VISION_TEXT, input_tokens=1, output_tokens=1,
            model="fake", provider_name=self.name, duration_ms=1.0,
        )

    async def stream(self, *a, **k):  # pragma: no cover
        if False:
            yield ""


class _FakeProviderRegistry:
    def __init__(self, p: _VisionProvider) -> None:
        self._p = p

    def get(self, name: str) -> _VisionProvider:
        return self._p

    def get_by_tier(self, tier: str) -> _VisionProvider:
        return self._p

    def all(self) -> list[_VisionProvider]:
        return [self._p]


@dataclass
class _Env:
    adapter: TelegramChannelAdapter
    bot: _FakeBot
    scanner: GatewayScanner
    backend: AsyncioBackend
    stream_registry: StreamRegistry
    provider: _VisionProvider


@pytest.fixture(autouse=True)
def _live_io():  # noqa: ANN202
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]
    yield
    TestModeGuard._active = prev  # type: ignore[attr-defined]


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


async def test_smoke_pdf_mode_b_through_telegram(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    ws = home / "workspace"
    ws.mkdir(parents=True)
    monkeypatch.setattr(StackowlHome, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(StackowlHome, "workspace", classmethod(lambda cls: ws))

    # A REAL, valid PDF with no extractable text → Mode A yields garbage → Mode B.
    from pypdf import PdfWriter

    pdf_path = ws / "scanned.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    with pdf_path.open("wb") as fh:
        writer.write(fh)

    adapter = TelegramChannelAdapter(TelegramSettings(allowed_user_ids=frozenset({USER_ID})))
    bot = _FakeBot()
    adapter._bot_app = _FakeBotApp(bot)
    adapter._bot_user_id = 999
    adapter._bot_username = ""
    provider = _VisionProvider(str(pdf_path))
    services = StepServices(
        provider_registry=_FakeProviderRegistry(provider),  # type: ignore[arg-type]
        tool_registry=ToolRegistry.with_defaults(),
        consent_gate=ConsequentialActionGate(),
        stream_registry=StreamRegistry(),
    )
    env = _Env(
        adapter=adapter, bot=bot, scanner=GatewayScanner(owl_registry=None),
        backend=AsyncioBackend(services=services), stream_registry=services.stream_registry,  # type: ignore[arg-type]
        provider=provider,
    )

    await _turn(env, "read this scanned pdf")

    # Mode A ran real pypdf on a real PDF, found no text, self-healed to Mode B,
    # which routed the document to the fake vision provider and returned its text.
    assert _VISION_TEXT in provider.result, provider.result
    assert "fake-vision" in provider.result, "Mode B must disclose the handling provider"
    assert bot.messages and bot.messages[-1]["chat_id"] == USER_ID
