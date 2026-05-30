"""E5-S8/S9 SMOKE — update_plan + todo driven AS THE USER, Telegram → end.

A real inbound Telegram message traverses the GENUINE path (adapter → scanner →
AsyncioBackend → execute._dispatch → ToolRegistry → update_plan / todo) against
the one shared PlanStore wired in with_defaults. Turn 1 sets a plan; turn 2
advances the active step. Read tools — no consent.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from stackowl.channels.telegram.adapter import TelegramChannelAdapter
from stackowl.channels.telegram.settings import TelegramSettings
from stackowl.config.test_mode import TestModeGuard
from stackowl.gateway.scanner import GatewayScanner
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import StreamRegistry
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

USER_ID = 898989


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
    run_task = asyncio.create_task(env.backend.run(state))
    out_task = asyncio.create_task(env.adapter.send(reader))
    await run_task
    await out_task
    env.stream_registry.remove(msg.session_id)
    return ""


async def test_smoke_plan_tools_through_telegram() -> None:
    adapter = TelegramChannelAdapter(TelegramSettings(allowed_user_ids=frozenset({USER_ID})))
    bot = _FakeBot()
    adapter._bot_app = _FakeBotApp(bot)
    adapter._bot_user_id = 999
    adapter._bot_username = ""
    provider = _ScriptedProvider()
    services = StepServices(
        provider_registry=_FakeProviderRegistry(provider),  # type: ignore[arg-type]
        tool_registry=ToolRegistry.with_defaults(),  # one shared PlanStore inside
        consent_gate=ConsequentialActionGate(),
        stream_registry=StreamRegistry(),
    )
    env = _Env(
        adapter=adapter, bot=bot, scanner=GatewayScanner(owl_registry=None),
        backend=AsyncioBackend(services=services), stream_registry=services.stream_registry,  # type: ignore[arg-type]
        provider=provider,
    )

    # Turn 1: lay out a plan via update_plan (whole-plan replace).
    provider.script.append((
        "update_plan",
        {"explanation": "do it in two steps", "plan": [
            {"id": "1", "content": "gather requirements", "status": "in_progress"},
            {"id": "2", "content": "implement", "status": "pending"},
        ]},
    ))
    await _turn(env, "make a plan")
    assert "gather requirements" in provider.results[0]
    assert "[>] 1" in provider.results[0]  # step 1 active

    # Turn 2: advance to step 2 via todo set_status (shared slot; step 1 demotes).
    provider.script.append(("todo", {"action": "set_status", "id": "2", "status": "in_progress"}))
    await _turn(env, "start step two")
    out = provider.results[1]
    assert "[>] 2" in out  # step 2 now active through the shared slot
    assert "implement" in out
    assert bot.messages and bot.messages[-1]["chat_id"] == USER_ID
