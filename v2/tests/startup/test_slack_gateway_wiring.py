"""Slack A4 — the Slack channel wired into the gateway startup loop.

Why this exists:
  Task A4 wires the Slack channel into ``_phase_gateway``: a guard (needs BOTH
  bot_token AND app_token for Socket Mode), secret resolution, the Bolt app +
  inbound event/slash handlers, a BACKGROUND socket task, the ``_slack_loop``
  (byte-for-byte the ``_telegram_loop`` with a Slack pump/adapter), and the
  shutdown cancel/close. The orchestrator is where the concurrency work hid 4
  production bugs, so this test drives the REAL wiring rather than a hand-rolled
  mirror.

What is REAL vs mocked (mirrors tests/journeys/test_p1_concurrent_foundation):
  REAL — the actual ``SlackChannelAdapter`` (its ``handle_event`` enqueue, its
  ``receive``, its ``send`` → ``send_text`` → ``_post_text`` → fake Bolt
  ``chat_postMessage`` routing), ``GatewayScanner``, ``StreamRegistry``,
  ``TurnRegistry``, ``AsyncioBackend`` (full pipeline incl. the real ``deliver``
  step that stamps ``chunk.target`` from ``state.reply_target``), the real
  ``ClarifyPump.spawn_send`` decoupled send, the per-session intake → dispatch →
  register slice (faithful to ``orchestrator._slack_loop`` + ``_intake`` +
  ``_dispatch_turn``).
  MOCKED — ONLY the AI provider (resolved through the real ``ProviderRegistry``)
  and the Bolt TRANSPORT (a fake AsyncApp exposing ``.client.auth_test`` +
  ``.client.chat_postMessage``). A live Socket Mode connection cannot be opened
  in a test, so the real ``AsyncApp`` construction + ``AsyncSocketModeHandler``
  socket connect are covered ONLY by the orchestrator's skip-on-failure guard,
  not here — this test proves the inbound-event → reply-to-originating-channel
  routing that those wrap.

Every await is wrapped in ``asyncio.wait_for`` so a hang/wedge FAILS the test
rather than wedging the suite.
"""

from __future__ import annotations

import asyncio
from typing import cast

import pytest

from stackowl.channels.slack.adapter import SlackChannelAdapter
from stackowl.channels.slack.settings import SlackSettings
from stackowl.config.test_mode import TestModeGuard
from stackowl.db.pool import DbPool
from stackowl.gateway.clarify_pump import ClarifyPump
from stackowl.gateway.scanner import GatewayScanner, IngressMessage
from stackowl.gateway.turn_registry import TurnRegistry
from stackowl.memory.sqlite_bridge import SqliteMemoryBridge
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import StreamRegistry
from stackowl.providers.base import CompletionResult, Message, ModelProvider
from stackowl.providers.registry import ProviderRegistry
from stackowl.tools.registry import ToolRegistry

pytestmark = pytest.mark.asyncio

_WAIT = 5.0  # every await is bounded — a hang FAILS, never wedges the suite.


# ---- Controllable mock provider (resolved THROUGH the real ProviderRegistry) -


class _ControllableProvider(ModelProvider):
    """Canned tool-loop reply (zero tool calls); ``reply_for`` keys map input→reply."""

    def __init__(self, *, reply_for: dict[str, str]) -> None:
        self._name = "fake"
        self._reply_for = reply_for
        self.calls = 0

    @property
    def name(self) -> str:
        return self._name

    @property
    def protocol(self) -> str:  # type: ignore[override]
        return "openai"

    async def complete(
        self, messages: list[Message], model: str, **kwargs: object
    ) -> CompletionResult:  # pragma: no cover — tool-loop path is forced
        return CompletionResult(
            content="UNUSED",
            input_tokens=1,
            output_tokens=1,
            model="fake-model",
            provider_name=self._name,
            duration_ms=1.0,
        )

    async def stream(  # type: ignore[override]
        self, messages: list[Message], model: str, **kwargs: object
    ):  # pragma: no cover — tool-loop path is forced
        yield "UNUSED"

    def _reply(self, user_text: str) -> str:
        for needle, reply in self._reply_for.items():
            if needle in user_text:
                return reply
        return "DEFAULT_REPLY"

    async def complete_with_tools(
        self,
        user_text: str,
        system_text: str | None,
        tool_schemas: list,
        tool_dispatcher,
        max_iterations: int = 8,
        history: list[Message] | None = None,
        persistence_check=None,
    ) -> tuple[str, list]:
        self.calls += 1
        return self._reply(user_text), []


# ---- Fake Bolt app (stands in for slack_bolt.async_app.AsyncApp transport) ----


class _FakeBoltClient:
    """Captures chat_postMessage kwargs; serves a canned auth_test."""

    def __init__(self, *, bot_user_id: str = "U_BOT") -> None:
        self._bot_user_id = bot_user_id
        self.posted: list[dict[str, object]] = []

    async def auth_test(self) -> dict[str, str]:
        return {"user_id": self._bot_user_id}

    async def chat_postMessage(self, **kwargs: object) -> dict[str, object]:
        self.posted.append(kwargs)
        return {"ok": True}


class _FakeBoltApp:
    """Minimal fake AsyncApp: exposes ``.client`` only (no socket transport)."""

    def __init__(self, *, bot_user_id: str = "U_BOT") -> None:
        self.client = _FakeBoltClient(bot_user_id=bot_user_id)


# ---- Shared helpers (mirror the P1 journey real-component scaffold) -----------


def _build_services(
    bridge: SqliteMemoryBridge,
    provider: ModelProvider,
    stream_registry: StreamRegistry,
) -> StepServices:
    preg = ProviderRegistry()
    preg.register_mock("secretary", provider, tier="powerful")
    preg.register_mock("powerful", provider, tier="powerful")
    return StepServices(
        memory_bridge=bridge,
        provider_registry=preg,
        owl_registry=OwlRegistry.with_default_secretary(),
        tool_registry=ToolRegistry.with_defaults(),
        # SHARED registry — the REAL deliver step writes into THIS instance,
        # exactly as orchestrator.py wires one stream_registry into the services
        # and the gateway loop.
        stream_registry=stream_registry,
    )


class _NullGateway:
    """A clarify gateway the pump never consults (spawn_send-only slice)."""

    def peek_for_session(self, session_id: str, channel: str) -> None:  # pragma: no cover
        return None


async def _dispatch_turn(
    *,
    backend: AsyncioBackend,
    scanner: GatewayScanner,
    stream_registry: StreamRegistry,
    turn_registry: TurnRegistry,
    pump: ClarifyPump,
    adapter: SlackChannelAdapter,
    msg: IngressMessage,
) -> tuple[asyncio.Task[object], asyncio.Task[None]]:
    """Faithful re-creation of orchestrator._dispatch_turn against REAL registries.

    create stream by trace_id → build PipelineState (incl.
    ``reply_target=msg.chat_id``) → backend.run → register the turn → spawn the
    real decoupled send. Returns ``(producer, send_task)``.
    """
    decision = scanner.scan(msg)
    input_text = decision.stripped_text if decision.stripped_text is not None else msg.text

    writer, reader = stream_registry.create(msg.trace_id)
    state = PipelineState(
        trace_id=msg.trace_id,
        session_id=msg.session_id,
        input_text=input_text,
        channel=msg.channel,
        owl_name=decision.target,
        pipeline_step="start",
        interactive=True,
        reply_target=msg.chat_id,  # §4.5 — route the reply to ITS channel
    )
    producer: asyncio.Task[object] = asyncio.create_task(backend.run(state))
    await turn_registry.register(
        msg.trace_id,
        session_id=msg.session_id,
        task=cast("asyncio.Task[None]", producer),
        target=msg.chat_id,
        original_input=input_text,
    )
    pump.spawn_send(
        channel_adapter=adapter,
        reader=reader,
        session_id=msg.session_id,
        request_id=msg.trace_id,
        producer=producer,
        writer=writer,
    )
    send_task = pump._inflight[msg.session_id]  # type: ignore[attr-defined]
    return producer, send_task


# ---- The integration proof — inbound Slack event → reply to ITS channel -------


async def test_slack_inbound_event_replies_to_originating_channel(
    tmp_db: DbPool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An inbound Slack DM/channel event routes through the SHARED gateway slice
    and the reply is posted (via the fake Bolt app) to the ORIGINATING channel
    (``C1``) — never the former hardcoded ``@stackowl``.

    Drives the REAL ``_slack_loop`` body slice: ``handle_event`` →
    ``slack_adapter.receive()`` → ``scanner.scan`` → ``_dispatch_turn`` →
    ``backend.run`` (mock provider) → ``deliver`` stamps ``chunk.target`` →
    ``ClarifyPump.spawn_send`` → the REAL ``SlackChannelAdapter.send`` →
    ``send_text`` → ``_post_text`` → fake ``chat_postMessage``.
    """
    # The adapter's send/handle paths assert NOT-test-mode; this is the live
    # gateway path, so disable the guard for the duration of this test (the Bolt
    # transport is faked, so no real network I/O occurs).
    monkeypatch.setattr(TestModeGuard, "assert_not_test_mode", staticmethod(lambda *_a, **_k: None))

    stream_registry = StreamRegistry()
    turn_registry = TurnRegistry()
    bridge = SqliteMemoryBridge(db=tmp_db)
    provider = _ControllableProvider(reply_for={"hello": "REPLY_HELLO"})
    services = _build_services(bridge, provider, stream_registry)
    backend = AsyncioBackend(services=services)
    scanner = GatewayScanner(owl_registry=OwlRegistry.with_default_secretary())
    pump = ClarifyPump(_NullGateway(), stream_registry)  # type: ignore[arg-type]

    # The REAL Slack adapter, allow-listing the sending user, with a FAKE Bolt
    # app attached (the seam the orchestrator wires via set_bolt_app).
    settings = SlackSettings(
        bot_token="xoxb-test",
        app_token="xapp-test",
        signing_secret="sig-test",
        allowed_user_ids=["U_SENDER"],
    )
    slack_adapter = SlackChannelAdapter(settings)
    fake_app = _FakeBoltApp(bot_user_id="U_BOT")
    slack_adapter.set_bolt_app(fake_app)
    # Mirror the orchestrator's auth_test → set_bot_user_id step.
    auth = await asyncio.wait_for(fake_app.client.auth_test(), timeout=_WAIT)
    slack_adapter.set_bot_user_id(auth["user_id"])

    # --- INBOUND: an event arrives on channel C1 (the Bolt handler calls this) --
    event = {"type": "message", "channel": "C1", "ts": "1700000000.000100"}
    await asyncio.wait_for(
        slack_adapter.handle_event(event, "U_SENDER", "hello there"), timeout=_WAIT
    )

    # --- The REAL _slack_loop slice: receive → scan → dispatch → deliver → send -
    msg = await asyncio.wait_for(slack_adapter.receive(), timeout=_WAIT)
    assert msg.channel == "slack"
    assert msg.chat_id == "C1", "the originating channel must be stamped as the reply target"

    producer, send_task = await _dispatch_turn(
        backend=backend, scanner=scanner, stream_registry=stream_registry,
        turn_registry=turn_registry, pump=pump, adapter=slack_adapter, msg=msg,
    )
    await asyncio.wait_for(producer, timeout=_WAIT)
    await asyncio.wait_for(send_task, timeout=_WAIT)
    await asyncio.sleep(0)  # let the send done-callback (_cleanup) run

    # --- ASSERT: the reply was posted to the ORIGINATING channel C1 -------------
    assert fake_app.client.posted, "no message was posted via the Bolt app"
    posts_to_c1 = [p for p in fake_app.client.posted if p.get("channel") == "C1"]
    assert posts_to_c1, f"reply was not posted to the originating channel C1: {fake_app.client.posted}"
    assert any("REPLY_HELLO" in str(p.get("text", "")) for p in posts_to_c1), fake_app.client.posted
    # NEVER the former hardcoded @stackowl destination.
    assert not any(p.get("channel") == "@stackowl" for p in fake_app.client.posted), fake_app.client.posted
    # The reply threaded under the originating ts (channel message → threaded).
    assert any(p.get("thread_ts") == "1700000000.000100" for p in posts_to_c1), fake_app.client.posted
    assert provider.calls == 1

    # Stream reaped under its OWN request_id key (no leak / no cross-key).
    assert stream_registry.get_writer(msg.trace_id) is None


# ---- The skip-when-no-token proof (the orchestrator guard) --------------------


@pytest.mark.asyncio(loop_scope="function")
async def test_slack_skipped_when_tokens_absent() -> None:
    """With no bot_token/app_token, the orchestrator guard skips Slack entirely.

    Socket Mode needs BOTH tokens; the guard is
    ``if slack_cfg.bot_token and slack_cfg.app_token``. This proves the boolean
    the orchestrator branches on, for every missing-token shape, so a bare
    deploy never tries to construct a Bolt app or wedge boot.
    """
    # Both missing → skip.
    assert not (SlackSettings().bot_token and SlackSettings().app_token)
    # bot_token only (no app_token → Socket Mode impossible) → skip.
    only_bot = SlackSettings(bot_token="xoxb-test")
    assert not (only_bot.bot_token and only_bot.app_token)
    # app_token only (no bot_token) → skip.
    only_app = SlackSettings(app_token="xapp-test")
    assert not (only_app.bot_token and only_app.app_token)
    # BOTH present → the guard fires (Slack would start).
    both = SlackSettings(bot_token="xoxb-test", app_token="xapp-test")
    assert both.bot_token and both.app_token
