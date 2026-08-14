"""E4-S1 SMOKE — memory add→search driven AS THE USER, Telegram input → end.

A real inbound Telegram update traverses the GENUINE path (adapter → scanner →
AsyncioBackend pipeline → execute._dispatch → ToolRegistry → memory). Turn 1 adds
a fact; turn 2 searches it back. Proves the tool is reachable by a real message,
tags its writes agent_self, and surfaces the mutation. The tri-store is faked
(the real LanceDB+Kuzu+ST-embedder is flaky on the Jetson box); the genuine
pipeline + tool + provenance path is real.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Literal

import pytest

from stackowl.channels.telegram.adapter import TelegramChannelAdapter
from stackowl.channels.telegram.settings import TelegramSettings
from stackowl.config.test_mode import TestModeGuard
from stackowl.gateway.scanner import GatewayScanner
from stackowl.memory.models import MemoryRecord, StagedFact
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import StreamRegistry
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

USER_ID = 858585


class _FakeBridge:
    def __init__(self) -> None:
        self.facts: list[StagedFact] = []

    async def stage(self, fact: StagedFact) -> None:
        self.facts.append(fact)

    async def delete(self, fact_id: str) -> None:
        self.facts = [f for f in self.facts if f.fact_id != fact_id]

    async def recall(self, query: str, limit: int = 10) -> list[MemoryRecord]:
        return [
            MemoryRecord(
                fact_id=f.fact_id, content=f.content, embedding=[0.0],
                embedding_model="fake", committed_at=datetime.now(UTC),
                source_type=f.source_type, source_ref=f.source_ref,
            )
            for f in self.facts if query.lower() in f.content.lower()
        ][:limit]

    async def list_staged(self, status: Literal["staged", "committed", "rejected"] = "staged") -> list[StagedFact]:
        return [] if status == "rejected" else list(self.facts)


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

    async def complete_with_tools(
        self, *, user_text, system_text, tool_schemas,
        tool_dispatcher, history=None, **_kwargs,
    ):  # noqa: ANN001
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
    _writer, reader = env.stream_registry.create(msg.trace_id)
    state = PipelineState(
        trace_id=msg.trace_id, session_key=msg.session_key, input_text=input_text,
        channel=msg.channel, owl_name=decision.target, pipeline_step="start",
    )
    run_task = asyncio.create_task(env.backend.run(state))
    out_task = asyncio.create_task(env.adapter.send(reader))
    await run_task
    await out_task
    env.stream_registry.remove(msg.trace_id)
    return ""


async def test_smoke_memory_add_then_search_through_telegram(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    # ISOLATE THE CURATED FILES FIRST. `memory(action="add")` writes
    # ~/.stackowl/memory/USER.md since D08.1 retargeted this tool at curated
    # memory. Without STACKOWL_HOME this smoke would write the test's fact into
    # the OPERATOR'S REAL profile — which is exactly what the skill_manage smoke
    # was found doing, having left a real skill behind on 2026-08-07.
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path / "home"))
    # The FactPromoter patch that used to sit here is GONE, not moved: D08.1's
    # abb08e09 removed the promoter from this tool when it was retargeted, so
    # the patch had been raising AttributeError and this smoke — the only
    # end-to-end proof that `memory` is reachable from a real Telegram message —
    # had been red ever since.
    bridge = _FakeBridge()
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
        memory_bridge=bridge,  # type: ignore[arg-type]
        db_pool=object(),  # type: ignore[arg-type]
    )
    env = _Env(
        adapter=adapter, bot=bot, scanner=GatewayScanner(owl_registry=None),
        backend=AsyncioBackend(services=services), stream_registry=services.stream_registry,  # type: ignore[arg-type]
        provider=provider,
    )

    # Turn 1: the agent remembers a fact (write path, tagged agent_self, visible).
    provider.script.append((
        "memory",
        {
            "action": "add",
            "content": "the deploy key is in vault path X",
            # Durability is REQUIRED since D08.1 — a write that cannot say how
            # long it stays true has nowhere to go.
            "durability": "permanent",
        },
    ))
    await _turn(env, "remember the deploy key location")
    # The exact contract, not a fuzzy substring: a curated write confirms it
    # SAVED and tells the user WHEN it reaches the prompt — D08.1 made that
    # second half mandatory, because the frozen snapshot means a write made now
    # is not visible to this conversation and the agent must say so.
    assert "Saved." in provider.results[0], provider.results[0]
    assert "next /new" in provider.results[0], provider.results[0]
    # Provenance now lives in the CURATED FILE, not a staged fact: the write
    # landed where the system prompt will actually read it.
    profile = tmp_path / "home" / "memory" / "USER.md"
    assert profile.exists(), f"curated profile not written at {profile}"
    assert "vault path X" in profile.read_text(encoding="utf-8")

    # Turn 2: the agent searches it back through the real pipeline.
    provider.script.append(("memory", {"action": "search", "query": "deploy key"}))
    await _turn(env, "what was the deploy key location")
    assert "vault path X" in provider.results[1], provider.results[1]
    assert bot.messages and bot.messages[-1]["chat_id"] == USER_ID
