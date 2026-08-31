"""SMOKE — the completion contract, driven AS THE USER from a Telegram update.

WHY THIS EXISTS. Three items shipped on 2026-08-31 — the achievement writer, the
judge, and the sticky-routing fix — and ALL THREE have live acceptance checks
with a ZERO DENOMINATOR: no chat turn has arrived since they went out, so their
paths have never been taken in production. "The turn succeeded" is not "my change
works", and neither is "no turn happened".

This cannot close a PRODUCTION check — a test is not traffic. What it does is
move all three from "never executed anywhere" to "executed through the real
seam", which is a different and much smaller risk.

THE PATH IS GENUINE: a real inbound Telegram update → TelegramChannelAdapter →
GatewayScanner → AsyncioBackend → triage/classify/assemble/execute/deliver, with
the real durable task store on a real (temporary) database. Only the MODEL is
scripted — the standing rule for gateway tests here is to mock the AI provider
and nothing else.

IT REPLAYS THE INCIDENT: an ordinary question, then the short follow-up "Give me
in pictures" that inherited a tool-free class it was never classified into.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Literal

import pytest

from stackowl.channels.telegram.adapter import TelegramChannelAdapter
from stackowl.channels.telegram.settings import TelegramSettings
from stackowl.config.test_mode import TestModeGuard
from stackowl.db.pool import DbPool
from stackowl.gateway.scanner import GatewayScanner
from stackowl.interaction.turn_achievement_judge import TurnAchievementJudge
from stackowl.interaction.turn_achievement_writer import (
    DEFAULT_ACHIEVEMENT,
    TurnAchievementWriter,
)
from stackowl.owls.sticky_route_cache import StickyRouteCache
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.durable.store import DurableTaskStore
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import TOOL_FREE_CLASSES, PipelineState
from stackowl.pipeline.streaming import StreamRegistry
from stackowl.providers.base import CompletionResult, Message, ModelProvider
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

USER_ID = 858586


class _ScriptedProvider(ModelProvider):
    """Answers by ROLE, so one provider serves the router, the writer, the judge
    and the assistant without the test caring about call order."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    @property
    def name(self) -> str:
        return "scripted"

    @property
    def protocol(self) -> Literal["openai", "anthropic", "gemini"]:
        return "openai"

    def _answer(self, messages: list[Message]) -> str:
        system = " ".join(m.content or "" for m in messages if m.role == "system")
        user = " ".join(m.content or "" for m in messages if m.role == "user")
        if "what would COUNT as a request being fulfilled" in system:
            self.calls.append("writer")
            return "a picture of the tree is delivered to the user"
        if "whether a stated criterion was met" in system:
            self.calls.append("judge")
            return "ACHIEVED ARTIFACT"
        if "intent" in system.lower() and "owl" in system.lower():
            self.calls.append("router")
            return "secretary\nconversational"
        self.calls.append(f"assistant:{user[:24]}")
        return "I'll draw this as an actual image for you."

    async def complete(
        self, messages: list[Message], model: str, **kwargs: object
    ) -> CompletionResult:
        return CompletionResult(
            content=self._answer(messages), input_tokens=1, output_tokens=1,
            model="scripted", provider_name=self.name, duration_ms=1.0,
        )

    async def stream(
        self, messages: list[Message], model: str, **kwargs: object
    ) -> AsyncIterator[str]:
        yield self._answer(messages)


class _FakeProviderRegistry:
    def __init__(self, p: _ScriptedProvider) -> None:
        self._p = p

    def get(self, *a: object, **k: object) -> _ScriptedProvider:
        return self._p

    def get_by_tier(self, tier: str) -> tuple[_ScriptedProvider, str]:
        # (provider, model), matching the REAL ProviderRegistry contract that
        # resolve_fixed_tier unpacks. A double that returns a bare provider is a
        # double that stopped resembling the real thing.
        return self._p, "scripted-model"


class _FakeBot:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_message(self, **kw: object) -> object:
        self.sent.append(str(kw.get("text", "")))
        return SimpleNamespace(message_id=1)

    async def send_chat_action(self, **kw: object) -> None:
        return None


class _FakeBotApp:
    def __init__(self, bot: _FakeBot) -> None:
        self.bot = bot


@dataclass
class _Env:
    adapter: TelegramChannelAdapter
    scanner: GatewayScanner
    backend: AsyncioBackend
    services: StepServices


@pytest.fixture(autouse=True)
def _live_io():  # noqa: ANN202
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]
    yield
    TestModeGuard._active = prev  # type: ignore[attr-defined]


async def _turn(env: _Env, text: str) -> PipelineState:
    update = SimpleNamespace(
        effective_message=SimpleNamespace(text=text),
        effective_user=SimpleNamespace(id=USER_ID),
        effective_chat=SimpleNamespace(id=USER_ID),
    )
    await env.adapter._handle_update(update, None)
    msg = await env.adapter.receive()
    decision = env.scanner.scan(msg)
    input_text = decision.stripped_text if decision.stripped_text is not None else msg.text
    _writer, reader = env.services.stream_registry.create(msg.trace_id)
    state = PipelineState(
        trace_id=msg.trace_id, session_key=msg.session_key, input_text=input_text,
        channel=msg.channel, owl_name=decision.target, pipeline_step="start",
    )
    run = asyncio.create_task(env.backend.run(state))
    out = asyncio.create_task(env.adapter.send(reader))
    final = await run
    await out
    env.services.stream_registry.remove(msg.trace_id)
    return final


@pytest.mark.asyncio
async def test_the_incident_replayed_through_the_real_gateway_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, tmp_db: DbPool,
) -> None:
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path / "home"))
    provider = _ScriptedProvider()
    adapter = TelegramChannelAdapter(TelegramSettings(allowed_user_ids=frozenset({USER_ID})))
    adapter._bot_app = _FakeBotApp(_FakeBot())
    adapter._bot_user_id = 999
    adapter._bot_username = ""

    registry = _FakeProviderRegistry(provider)
    services = StepServices(
        provider_registry=registry,  # type: ignore[arg-type]
        tool_registry=ToolRegistry.with_defaults(),
        consent_gate=ConsequentialActionGate(),
        stream_registry=StreamRegistry(),
        sticky_route_cache=StickyRouteCache(),
        durable_task_store=DurableTaskStore(tmp_db),
        turn_achievement_writer=TurnAchievementWriter(registry),  # type: ignore[arg-type]
        turn_achievement_judge=TurnAchievementJudge(registry),  # type: ignore[arg-type]
        db_pool=tmp_db,
    )
    env = _Env(
        adapter=adapter,
        scanner=GatewayScanner(owl_registry=None),
        backend=AsyncioBackend(services=services),
        services=services,
    )

    first = await _turn(env, "Explain me in easy way hiw to remember bfs for tree in python")
    second = await _turn(env, "Give me in pictures")

    # 1. THE TRIAGE FIX, through the real path. Whatever the first turn was
    # classified as, the short follow-up must not be born unable to act.
    assert second.intent_class not in TOOL_FREE_CLASSES, (
        f"the follow-up inherited a tool-free class ({second.intent_class}) — "
        "this is the 'Give me in pictures' failure, through the genuine path"
    )
    assert first is not None


@pytest.mark.asyncio
async def test_the_writer_and_judge_are_reachable_from_the_real_enqueue(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, tmp_db: DbPool,
) -> None:
    """The criterion is written, lands on the row, and the judge then reads it.

    SCOPED HONESTLY. The first test drives the whole gateway path, but the
    durable row is created by the ORCHESTRATOR's _dispatch_turn, not by
    backend.run — driving the backend alone never enqueues, which this test
    discovered rather than assumed. So this calls enqueue_turn_task and
    complete_turn_task exactly as orchestrator.py does, with the real store on a
    real database. It proves the two shadow paths EXECUTE and that their output
    reaches the row; it does not prove the orchestrator calls them, which is a
    read of five lines of wiring rather than a behaviour.
    """
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path / "home"))
    from stackowl.pipeline.durable.turn_task import (
        complete_turn_task,
        enqueue_turn_task,
    )

    provider = _ScriptedProvider()
    registry = _FakeProviderRegistry(provider)
    store = DurableTaskStore(tmp_db)
    services = StepServices(
        provider_registry=registry,  # type: ignore[arg-type]
        turn_achievement_writer=TurnAchievementWriter(registry),  # type: ignore[arg-type]
        turn_achievement_judge=TurnAchievementJudge(registry),  # type: ignore[arg-type]
        durable_task_store=store,
        db_pool=tmp_db,
    )
    from stackowl.pipeline.services import reset_services, set_services

    token = set_services(services)
    try:
        trace = "smoke-achievement-1"
        await enqueue_turn_task(
            store, trace_id=trace, goal="Give me in pictures", channel="telegram",
            chat_id=USER_ID, session_key="s", owl_name="secretary",
            loop_produces=False, loop=None,
            achievement_writer=services.turn_achievement_writer,
        )
        for _ in range(60):
            await asyncio.sleep(0.05)
            if "writer" in provider.calls:
                break
        assert "writer" in provider.calls, "the achievement writer never ran"

        rows = await tmp_db.fetch_all(
            "SELECT achievement FROM tasks WHERE task_id = ?", (trace,)
        )
        assert rows, "the turn was never enqueued"
        assert rows[0]["achievement"] != DEFAULT_ACHIEVEMENT, (
            "the criterion never reached the row — the judge would have nothing to read"
        )

        # And the judge then reads that row back and returns a verdict.
        await complete_turn_task(
            store, trace_id=trace,
            result="I'll draw this as an actual image for you.",
            state=SimpleNamespace(ran_effect_classes=(), responses=()),
        )
        for _ in range(60):
            await asyncio.sleep(0.05)
            if "judge" in provider.calls:
                break
        assert "judge" in provider.calls, "the judge never ran at completion"
    finally:
        reset_services(token)
