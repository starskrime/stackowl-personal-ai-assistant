"""E8-S2 SMOKE — mixture_of_agents driven AS THE USER, Telegram input → verdict.

A real inbound Telegram update traverses the GENUINE path (TelegramChannelAdapter →
GatewayScanner → AsyncioBackend pipeline → execute._dispatch → ToolRegistry →
MixtureOfAgentsTool → REAL ``ProviderRegistry.healthy_distinct()`` fan-out over
several providers → REAL ``ParliamentSynthesizer.synthesize_positions`` → the
synthesized verdict travels back as the parent's final answer → delivered to the
user over Telegram).

The owl turn (owl=secretary) emits a ``mixture_of_agents`` tool call
``{"question": ...}``. That call runs through the real pipeline and the real tool,
which fans out across the registry's distinct providers and aggregates via the real
synthesizer, then returns the verdict as the final answer.

REAL: the whole pipeline, the ToolRegistry + MixtureOfAgentsTool, the
``ProviderRegistry`` (with several distinct providers + genuine breakers), the
``ParliamentSynthesizer``. MOCKED: ONLY the AI providers — the secretary provider
(scripted: emits the tool call, then returns the tool's synthesized answer), the
MoA proposer providers (canned positions), the powerful-tier synthesizer provider
(canned CONSENSUS markers), and the Telegram bot transport (captures outbound text
in-process). The tool + synthesizer themselves are NOT stubbed.

This test FAILS if MoA is unwired: if the tool is not registered, or
``healthy_distinct``/``synthesize_positions`` are missing, the verdict never
reaches the user. A second case proves the thin-roster refusal path end-to-end.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from stackowl.channels.telegram.adapter import TelegramChannelAdapter
from stackowl.channels.telegram.settings import TelegramSettings
from stackowl.config.test_mode import TestModeGuard
from stackowl.gateway.scanner import GatewayScanner
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import StreamRegistry
from stackowl.providers.base import CompletionResult, Message
from stackowl.providers.circuit_breaker import CircuitState
from stackowl.providers.registry import ProviderRegistry
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

USER_ID = 717171

QUESTION = "Kuzu vs LanceDB for embeddings?"
SYNTH_CONSENSUS = "both are viable; choose by workload."
SYNTH_RECO = "use LanceDB for vectors, Kuzu for graph."


class _FakeBot:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_message(self, chat_id, text, parse_mode=None, reply_markup=None):  # noqa: ANN001
        self.messages.append({"chat_id": chat_id, "text": text})

    async def answer_callback_query(self, callback_id, text=None):  # noqa: ANN001
        pass


class _FakeBotApp:
    def __init__(self, bot: _FakeBot) -> None:
        self.bot = bot

    def add_handler(self, handler: object) -> None:
        pass


class _SecretaryProvider:
    """Scripted secretary: emits a mixture_of_agents call, returns its verdict.

    The pipeline resolves THIS provider by the owl name 'secretary'. On the tool
    loop it dispatches mixture_of_agents and surfaces the tool's synthesized answer
    as the user-facing reply. Its plain ``complete`` doubles as a MoA proposer.
    """

    protocol = "anthropic"

    def __init__(self) -> None:
        self.tool_results: list[str] = []

    @property
    def name(self) -> str:
        return "secretary"

    async def complete_with_tools(self, *, user_text, system_text, tool_schemas, tool_dispatcher, history=None):  # noqa: ANN001
        out = await tool_dispatcher("mixture_of_agents", {"question": user_text})
        self.tool_results.append(out)
        record = json.loads(out).get("record", {})
        # Surface the synthesized verdict (or the structured refusal detail).
        final = str(record.get("answer") or record.get("detail") or out)
        return (final, [{"name": "mixture_of_agents", "args": {"question": user_text}, "result": out}])

    async def complete(self, messages: list[Message], model: str, **kwargs: object) -> CompletionResult:
        return CompletionResult(
            content="secretary's position: it depends on the access pattern.",
            input_tokens=5, output_tokens=9, model="secretary-model",
            provider_name="secretary", duration_ms=1.0,
        )

    async def stream(self, *a, **k):  # pragma: no cover
        if False:
            yield ""


class _ProposerProvider:
    """A distinct MoA proposer returning a canned position."""

    protocol = "openai"

    def __init__(self, label: str) -> None:
        self._label = label

    @property
    def name(self) -> str:
        return self._label

    async def complete(self, messages: list[Message], model: str, **kwargs: object) -> CompletionResult:
        return CompletionResult(
            content=f"{self._label}'s position: prefer the right tool per workload.",
            input_tokens=6, output_tokens=10, model=f"{self._label}-model",
            provider_name=self._label, duration_ms=1.0,
        )

    async def stream(self, *a, **k):  # pragma: no cover
        if False:
            yield ""


class _SynthProvider:
    """Powerful-tier aggregator returning canned CONSENSUS markers."""

    protocol = "openai"

    @property
    def name(self) -> str:
        return "synth"

    async def complete(self, messages: list[Message], model: str, **kwargs: object) -> CompletionResult:
        content = (
            f"CONSENSUS: {SYNTH_CONSENSUS}\n"
            f"RECOMMENDATION: {SYNTH_RECO}\n◆"
        )
        return CompletionResult(
            content=content, input_tokens=8, output_tokens=14, model="synth-model",
            provider_name="synth", duration_ms=1.0,
        )

    async def stream(self, *a, **k):  # pragma: no cover
        if False:
            yield ""


@dataclass
class _Env:
    adapter: TelegramChannelAdapter
    bot: _FakeBot
    scanner: GatewayScanner
    backend: AsyncioBackend
    stream_registry: StreamRegistry
    secretary: _SecretaryProvider


@pytest.fixture(autouse=True)
def _live_io():  # noqa: ANN202
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]
    yield
    TestModeGuard._active = prev  # type: ignore[attr-defined]


def _build_env(registry: ProviderRegistry, secretary: _SecretaryProvider) -> _Env:
    adapter = TelegramChannelAdapter(TelegramSettings(allowed_user_ids=frozenset({USER_ID})))
    bot = _FakeBot()
    adapter._bot_app = _FakeBotApp(bot)  # type: ignore[assignment]
    adapter._bot_user_id = 999
    adapter._bot_username = ""

    owl_registry = OwlRegistry.with_default_secretary()
    services = StepServices(
        provider_registry=registry,
        tool_registry=ToolRegistry.with_defaults(),
        consent_gate=ConsequentialActionGate(),
        stream_registry=StreamRegistry(),
        owl_registry=owl_registry,
    )
    return _Env(
        adapter=adapter, bot=bot, scanner=GatewayScanner(owl_registry=None),
        backend=AsyncioBackend(services=services),
        stream_registry=services.stream_registry,  # type: ignore[arg-type]
        secretary=secretary,
    )


async def _turn(env: _Env, text: str) -> None:
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


async def test_smoke_mixture_of_agents_verdict_through_telegram() -> None:
    secretary = _SecretaryProvider()
    registry = ProviderRegistry()
    registry.register_mock("secretary", secretary, tier="standard")  # type: ignore[arg-type]
    registry.register_mock("scout", _ProposerProvider("scout"), tier="fast")  # type: ignore[arg-type]
    registry.register_mock("synth", _SynthProvider(), tier="powerful")  # type: ignore[arg-type]
    env = _build_env(registry, secretary)

    await _turn(env, QUESTION)

    # (1) The mixture_of_agents tool ran through the REAL pipeline (reached via
    # execute._dispatch → ToolRegistry → MixtureOfAgentsTool, not a direct call).
    assert secretary.tool_results, "secretary never reached mixture_of_agents via the pipeline"
    record = json.loads(secretary.tool_results[0])["record"]
    assert record["status"] == "ok", record

    # (2) REAL fan-out consulted all 3 distinct healthy providers, real synthesis.
    assert record["ensemble_size"] == 3, record
    assert record["degraded_ensemble"] is False, record
    assert SYNTH_CONSENSUS in str(record["answer"]), record

    # (3) The synthesized verdict reached the USER over Telegram.
    assert env.bot.messages, "no outbound Telegram message"
    delivered = "\n".join(m["text"] for m in env.bot.messages if m["chat_id"] == USER_ID)
    assert SYNTH_CONSENSUS.split(";")[0] in delivered, delivered


async def test_smoke_mixture_of_agents_thin_roster_refusal_through_telegram() -> None:
    secretary = _SecretaryProvider()
    registry = ProviderRegistry()
    registry.register_mock("secretary", secretary, tier="standard")  # type: ignore[arg-type]
    # Second provider exists but its breaker is OPEN → only 1 healthy distinct.
    registry.register_mock("scout", _ProposerProvider("scout"), tier="fast")  # type: ignore[arg-type]
    breaker = registry.get_circuit_breaker("scout")
    assert breaker is not None
    breaker._state = CircuitState.OPEN  # type: ignore[attr-defined]
    env = _build_env(registry, secretary)

    await _turn(env, QUESTION)

    # The tool refused on the thin roster (only secretary healthy) — structured.
    record = json.loads(secretary.tool_results[0])["record"]
    assert record["status"] == "insufficient_roster", record
    assert record["available"] == 1, record

    # The refusal reached the USER over Telegram.
    delivered = "\n".join(m["text"] for m in env.bot.messages if m["chat_id"] == USER_ID)
    assert "answer the question directly" in delivered.lower(), delivered
