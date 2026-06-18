"""E5-C SMOKE — Telegram inline tap-button resolves a parked clarify turn.

A real inbound Telegram message runs a turn whose owl calls ``clarify`` WITH
choices. The real TelegramChannelAdapter.send_clarify renders inline buttons
(callback_data ``clarify:{id}:{idx}``). We then drive the REAL
``TelegramClarifyResolver.handle_callback`` with the delivered "blue" button's
callback_data — exactly as the CallbackRouter does on a tap — and assert the
parked turn resumes in-place with the chosen text.

Genuine path: ClarifyTool -> ClarifyGateway.ask(blocking) -> wait_for_answer
(parked) -> adapter.send_clarify (inline keyboard delivered) -> button tap ->
TelegramClarifyResolver -> peek + try_resolve -> the parked turn continues.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from stackowl.channels.telegram.adapter import TelegramChannelAdapter
from stackowl.channels.telegram.clarify import TelegramClarifyResolver
from stackowl.channels.telegram.settings import TelegramSettings
from stackowl.config.test_mode import TestModeGuard
from stackowl.interaction.clarify_gateway import ClarifyGateway
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import StreamRegistry
from stackowl.tools.interaction.clarify import ClarifyTool
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

USER_ID = 545454


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

    async def complete_with_tools(  # noqa: ANN001
        self, *, user_text, system_text, tool_schemas, tool_dispatcher,
        history=None, persistence_check=None, **kwargs,
    ):
        name, args = self.script.pop(0)
        out = await tool_dispatcher(name, args)  # PARKS here for blocking clarify
        self.results.append(out)
        return (f"Acting on -> {out}", [{"name": name, "args": args, "result": out}])

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

    def get_with_cascade(self, tier: str) -> _ScriptedProvider:
        return self._p


@dataclass
class _Env:
    adapter: TelegramChannelAdapter
    bot: _FakeBot
    backend: AsyncioBackend
    stream_registry: StreamRegistry
    provider: _ScriptedProvider
    gateway: ClarifyGateway
    resolver: TelegramClarifyResolver


@pytest.fixture(autouse=True)
def _live_io():  # noqa: ANN202
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]
    yield
    TestModeGuard._active = prev  # type: ignore[attr-defined]


def _build_env() -> _Env:
    adapter = TelegramChannelAdapter(TelegramSettings(allowed_user_ids=frozenset({USER_ID})))
    bot = _FakeBot()
    adapter._bot_app = _FakeBotApp(bot)
    adapter._bot_user_id = 999
    adapter._bot_username = ""
    provider = _ScriptedProvider()
    gateway = ClarifyGateway()
    gateway.register_adapter("telegram", adapter)
    registry = ToolRegistry.with_defaults()
    registry.register(ClarifyTool(timeout_s=30.0), replace=True)
    services = StepServices(
        provider_registry=_FakeProviderRegistry(provider),  # type: ignore[arg-type]
        tool_registry=registry,
        consent_gate=ConsequentialActionGate(),
        stream_registry=StreamRegistry(),
        clarify_gateway=gateway,
    )
    return _Env(
        adapter=adapter, bot=bot, backend=AsyncioBackend(services=services),
        stream_registry=services.stream_registry, provider=provider, gateway=gateway,  # type: ignore[arg-type]
        resolver=TelegramClarifyResolver(gateway),
    )


async def _inbound(env: _Env, text: str) -> object:
    update = SimpleNamespace(
        effective_message=SimpleNamespace(text=text),
        effective_user=SimpleNamespace(id=USER_ID),
        effective_chat=SimpleNamespace(id=USER_ID),
    )
    await env.adapter._handle_update(update, None)
    return await env.adapter.receive()


def _state_for(msg: object) -> PipelineState:
    return PipelineState(
        trace_id=msg.trace_id, session_id=msg.session_id, input_text=msg.text,  # type: ignore[attr-defined]
        channel=msg.channel, owl_name="default", pipeline_step="start",  # type: ignore[attr-defined]
        interactive=True,
    )


async def _wait_until(predicate, *, tries: int = 300) -> bool:  # noqa: ANN001
    for _ in range(tries):
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return False


def _callback_data_for(markup: object, label: str) -> str:
    """Pull the callback_data of the inline button whose text == label."""
    for row in markup.inline_keyboard:  # type: ignore[attr-defined]
        for button in row:
            if button.text == label:
                return button.callback_data
    raise AssertionError(f"no inline button labelled {label!r} in {markup!r}")


async def test_smoke_clarify_tap_button_resumes_turn() -> None:
    env = _build_env()
    msg = await _inbound(env, "pick a colour for me")
    env.provider.script.append(
        ("clarify", {"question": "Which colour?", "choices": ["red", "blue", "green"]})
    )
    env.stream_registry.create(msg.trace_id)  # type: ignore[attr-defined]

    run_task = asyncio.create_task(env.backend.run(_state_for(msg)))

    # The turn parks and the inline keyboard is delivered to the asking user.
    delivered = await _wait_until(lambda: bool(env.bot.messages) and env.bot.messages[-1]["reply_markup"])
    assert delivered, "clarify inline keyboard was never delivered"
    assert not run_task.done(), "turn should be parked awaiting the tap"
    sent = env.bot.messages[-1]
    assert sent["chat_id"] == USER_ID  # targeted the asking user's chat
    markup = sent["reply_markup"]
    assert markup is not None, "expected an inline keyboard, got plain text"

    # Tap the "blue" button -> drive the REAL resolver exactly as the router does.
    cb_data = _callback_data_for(markup, "blue")
    assert cb_data.startswith("clarify:") and cb_data.endswith(":1")  # idx of "blue"
    await env.resolver.handle_callback("cb-1", cb_data)

    await asyncio.wait_for(run_task, timeout=5.0)
    assert env.provider.results, "turn did not resume after the tap"
    assert "blue" in env.provider.results[0], "the tapped choice did not reach the model"


async def test_smoke_stale_tap_is_ignored() -> None:
    """A tap whose clarify_id is unknown (already answered/superseded) is a no-op."""
    env = _build_env()
    # No pending clarify registered -> peek returns None -> resolver ignores it.
    await env.resolver.handle_callback("cb-x", "clarify:nonexistentid:0")
    # Nothing to assert beyond "did not raise"; the gateway has no pending entry.
    assert env.gateway.try_resolve("someone", "telegram", "x") is None
