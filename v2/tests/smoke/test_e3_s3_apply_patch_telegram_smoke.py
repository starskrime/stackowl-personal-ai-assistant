"""E3-S3 SMOKE — apply_patch (multi-file) + full undo, Telegram input → end.

A real inbound Telegram update traverses the GENUINE path (adapter → scanner →
AsyncioBackend pipeline → execute._dispatch → ToolRegistry → apply_patch),
applies a 2-file V4A patch + creates a file, then a second turn undoes the WHOLE
patch via undo_write (one token reverts all files — M1). apply_patch is severity
'write' (ungated); atomic rollback + group undo are the safety net.
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

USER_ID = 838383


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


def _patch(*body: str) -> str:
    return "*** Begin Patch\n" + "\n".join(body) + "\n*** End Patch\n"


async def test_smoke_apply_patch_then_undo_through_telegram(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    ws = home / "workspace"
    ws.mkdir(parents=True)
    monkeypatch.setattr(StackowlHome, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(StackowlHome, "workspace", classmethod(lambda cls: ws))
    a = ws / "a.txt"
    b = ws / "b.txt"
    a.write_text("alpha\n")
    b.write_text("beta\n")
    created = ws / "c.txt"

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

    patch = _patch(
        "*** Update File: " + str(a), "@@", "-alpha", "+ALPHA",
        "*** Update File: " + str(b), "@@", "-beta", "+BETA",
        "*** Add File: " + str(created), "+made by patch",
    )
    # Turn 1: apply the multi-file patch through the real pipeline.
    provider.script.append(("apply_patch", {"patch": patch}))
    await _turn(env, "apply this refactor patch")
    assert a.read_text() == "ALPHA\n", "patch did not apply to a"
    assert b.read_text() == "BETA\n", "patch did not apply to b"
    assert created.exists(), "patch did not create c"
    assert "Undo token:" in provider.results[0], provider.results[0]
    token = provider.results[0].split("Undo token:", 1)[1].split()[0]

    # Turn 2: one undo reverts the ENTIRE patch (all files + removes the created one).
    provider.script.append(("undo_write", {"token": token}))
    await _turn(env, "undo that patch")
    assert a.read_text() == "alpha\n" and b.read_text() == "beta\n", "multi-file undo incomplete"
    assert not created.exists(), "undo did not remove the created file"
    assert bot.messages and bot.messages[-1]["chat_id"] == USER_ID
