"""E5 clarify SMOKE — in-process BLOCKING pause/resume, driven over Telegram.

A real inbound Telegram message runs a turn whose owl calls ``clarify``. With
blocking-await the tool PARKS the turn (the provider's ``complete_with_tools``
coroutine is suspended inside the tool dispatch) and the question is delivered
out-of-band via the channel adapter. The pump's resolve-router (simulated here
by calling :meth:`ClarifyGateway.try_resolve`, exactly as the orchestrator loop
does on the user's reply) wakes the parked waiter, and the SAME turn resumes
with the user's answer threaded into the model's continuation.

This exercises the genuine path: ClarifyTool -> ClarifyGateway.ask(blocking) ->
wait_for_answer (parked) -> adapter.send_clarify (delivered) -> try_resolve sets
the event -> the turn continues. Also covers the graceful-timeout and the
non-interactive (cron) sentinel branches.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from stackowl.channels.telegram.adapter import TelegramChannelAdapter
from stackowl.channels.telegram.settings import TelegramSettings
from stackowl.config.test_mode import TestModeGuard
from stackowl.infra.trace import TraceContext
from stackowl.interaction.clarify_gateway import ClarifyGateway
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import StreamRegistry
from stackowl.tools.interaction.clarify import ClarifyTool
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

USER_ID = 767676


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
    """Calls the scripted tool (clarify) then echoes the result into the reply."""

    protocol = "anthropic"

    def __init__(self) -> None:
        self.script: list[tuple[str, dict]] = []
        self.results: list[str] = []

    async def complete_with_tools(self, *, user_text, system_text, tool_schemas, tool_dispatcher):  # noqa: ANN001
        name, args = self.script.pop(0)
        out = await tool_dispatcher(name, args)  # PARKS here for blocking clarify
        self.results.append(out)
        return (f"Acting on your answer -> {out}", [{"name": name, "args": args, "result": out}])

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
    backend: AsyncioBackend
    stream_registry: StreamRegistry
    provider: _ScriptedProvider
    gateway: ClarifyGateway


@pytest.fixture(autouse=True)
def _live_io():  # noqa: ANN202
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]
    yield
    TestModeGuard._active = prev  # type: ignore[attr-defined]


def _build_env(*, timeout_s: float = 30.0) -> _Env:
    adapter = TelegramChannelAdapter(TelegramSettings(allowed_user_ids=frozenset({USER_ID})))
    bot = _FakeBot()
    adapter._bot_app = _FakeBotApp(bot)
    adapter._bot_user_id = 999
    adapter._bot_username = ""
    provider = _ScriptedProvider()
    gateway = ClarifyGateway()
    gateway.register_adapter("telegram", adapter)
    registry = ToolRegistry.with_defaults()
    # with_defaults() already registers a clarify tool; swap in one with a test
    # timeout so the graceful-timeout case doesn't wait the 30-minute default.
    registry.register(ClarifyTool(timeout_s=timeout_s), replace=True)
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
        interactive=True,  # simulates a real Telegram user present to answer clarify
    )


async def _wait_until(predicate, *, tries: int = 300) -> bool:  # noqa: ANN001
    for _ in range(tries):
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return False


async def test_smoke_clarify_blocks_then_resumes_in_turn() -> None:
    """Turn parks on clarify, question is delivered, the reply resumes it in-turn."""
    env = _build_env()
    msg = await _inbound(env, "help me pick")
    env.provider.script.append(
        ("clarify", {"question": "Which colour do you want?", "choices": ["red", "blue"]})
    )
    _w, _r = env.stream_registry.create(msg.session_id)  # type: ignore[attr-defined]

    run_task = asyncio.create_task(env.backend.run(_state_for(msg)))

    # The turn must PARK: the question is delivered but the run has not finished.
    delivered = await _wait_until(
        lambda: any("Which colour" in m["text"] for m in env.bot.messages)
    )
    assert delivered, "clarify question was never delivered to the channel"
    assert not run_task.done(), "turn should be parked awaiting the user's answer"

    # The pump's resolve-router fires on the user's reply -> wakes the parked turn.
    resolved = env.gateway.try_resolve(msg.session_id, "telegram", "blue")  # type: ignore[attr-defined]
    assert resolved is not None
    assert resolved.event is not None and resolved.event.is_set()  # blocking resolve

    await asyncio.wait_for(run_task, timeout=5.0)
    assert env.provider.results, "turn did not resume"
    assert "blue" in env.provider.results[0], "the user's answer did not reach the model"


async def test_smoke_clarify_graceful_timeout() -> None:
    """No reply within the window -> structured graceful-timeout result, turn ends."""
    env = _build_env(timeout_s=0.05)
    msg = await _inbound(env, "decide for me")
    env.provider.script.append(("clarify", {"question": "Proceed with the risky step?"}))
    env.stream_registry.create(msg.session_id)  # type: ignore[attr-defined]

    await asyncio.wait_for(env.backend.run(_state_for(msg)), timeout=5.0)
    assert env.provider.results
    assert "did not reply" in env.provider.results[0].lower()


async def test_smoke_clarify_non_interactive_sentinel() -> None:
    """Under a non-interactive (cron) context clarify never parks; it ABORT-warns."""
    env = _build_env()
    # Drive the tool under a non-interactive trace (cron/parliament/delegation):
    # it must return the ABORT sentinel without registering a pending clarify.
    token = TraceContext.start(
        "cron-session", trace_id="t-cron", interactive=False, channel="telegram",
    )
    try:
        res = await ClarifyTool().execute(question="Delete everything?")
    finally:
        TraceContext.reset(token)
    assert "non-interactive" in (res.output + (res.error or "")).lower()
    # Nothing was registered / no waiter parked.
    assert env.gateway.try_resolve("cron-session", "telegram", "whatever") is None
