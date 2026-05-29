"""E3-S2 SMOKE — edit + undo_write driven AS THE USER, Telegram input → end.

NOT a direct execute() call. A real inbound Telegram update traverses the GENUINE
path (adapter → scanner → AsyncioBackend pipeline → execute._dispatch →
ToolRegistry → edit) and edits a real workspace file; a second turn calls
undo_write to restore the pre-image. edit is severity 'write' (StackOwl's consent
gate prompts only for 'consequential'), so no consent round-trip — the undo token
+ surfaced diff are the safety net (operator's diff+undo scope choice).
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

USER_ID = 828282


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
    """Dispatches a queued (tool_name, args) on each turn; records the result."""

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
    provider: _ScriptedProvider


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
    before = len(env.bot.messages)
    run_task = asyncio.create_task(env.backend.run(state))
    out_task = asyncio.create_task(env.adapter.send(reader))
    await run_task
    await out_task
    env.stream_registry.remove(msg.session_id)
    return "".join(m["text"] for m in env.bot.messages[before:] if m["reply_markup"] is None)


async def test_smoke_edit_then_undo_through_telegram(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    ws = home / "workspace"
    ws.mkdir(parents=True)
    monkeypatch.setattr(StackowlHome, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(StackowlHome, "workspace", classmethod(lambda cls: ws))
    target = ws / "greeting.py"
    target.write_text("def greet():\n    return 'hello'\n")

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
    )
    env = _Env(
        adapter=adapter, bot=bot, scanner=GatewayScanner(owl_registry=None),
        backend=AsyncioBackend(services=services), stream_registry=services.stream_registry,  # type: ignore[arg-type]
        provider=provider,
    )

    # Turn 1: edit the file through the real pipeline.
    provider.script.append((
        "edit",
        {"path": str(target), "old_string": "return 'hello'", "new_string": "return 'bonjour'"},
    ))
    out1 = await _turn(env, "change the greeting to bonjour")
    assert "bonjour" in target.read_text(), "edit did not land on disk"
    assert "Undo token:" in provider.results[0], provider.results[0]
    assert "@@" in provider.results[0] or "---" in provider.results[0]  # unified diff surfaced
    assert out1.strip() and bot.messages[-1]["chat_id"] == USER_ID

    # Turn 2: undo_write restores the pre-image — "one undo away".
    provider.script.append(("undo_write", {}))
    await _turn(env, "undo that")
    assert "hello" in target.read_text() and "bonjour" not in target.read_text(), "undo_write did not restore"
