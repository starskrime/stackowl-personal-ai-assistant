"""GATEWAY INTEGRATION (E8-S0cost) — active per-turn cost-pause via clarify.

The business requirement (E8 operator fork-1 / addendum): when a single
INTERACTIVE turn's accumulated LLM spend crosses the soft per-turn budget, the
assistant ASKS the user ("This turn has spent about $X so far. Continue?") via
the E5 clarify round-trip BEFORE running the next expensive op
(``mixture_of_agents`` fan-out). The user's tap decides: "Stop" ABORTS the
expensive op (NO proposer fan-out runs); "Continue" PROCEEDS.

This is NOT a per-tool unit test. It drives the GENUINE pause-and-resume path the
live Telegram loop runs — exactly the J6 harness — and mocks ONLY the AI:

REAL (everything except the AI provider): the AsyncioBackend pipeline, the REAL
``ToolRegistry`` + REAL ``MixtureOfAgentsTool``, a REAL pre-seeded ``CostTracker``
(its in-memory per-turn running total is ALREADY over a low threshold), the REAL
``CostPauseGuard`` wired onto ``StepServices.cost_pause_guard``, the REAL
``ClarifyGateway`` (in-process suspend/resume — an ``asyncio.Event`` parks the
turn mid-dispatch and the tap wakes it IN THE SAME TURN), the REAL ``ClarifyPump``
(routes the user's tap to the parked waiter), the REAL ``TraceContext``
interactivity gate, and the Telegram adapter inbound/outbound transport.

FAKED — ONLY the AI: a scripted secretary that, on its turn, calls the REAL
``mixture_of_agents`` tool (which hits the REAL cost-pause guard → REAL clarify
park); and the MoA "proposer" providers, which RECORD whether they were called.
Because the gate aborts BEFORE the fan-out, a "Stop" turn must show ZERO proposer
calls — the abort is asserted from REAL effects, not a return shape.

Scenarios:
  A (Stop → ABORT): the turn is over budget → MoA triggers a REAL "Continue?"
    clarify delivered to the user's chat → the user taps "Stop" → the MoA op
    ABORTS (the tool result is the cost-budget-stopped record AND no proposer's
    ``complete`` was ever called).
  B (Continue → PROCEED): same setup → the user taps "Continue" → the fan-out
    RUNS (the proposers ARE called and a synthesized verdict comes back).

FAIL if the pause isn't wired: if MoA ran the fan-out WITHOUT asking, scenario A's
"no proposer calls" assertion fails; if the tap never resumed the turn, the run
would hang past the bounded timeout.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from stackowl.channels.telegram.adapter import TelegramChannelAdapter
from stackowl.channels.telegram.settings import TelegramSettings
from stackowl.config.test_mode import TestModeGuard
from stackowl.events.bus import EventBus
from stackowl.gateway.clarify_pump import ClarifyPump
from stackowl.gateway.scanner import GatewayScanner
from stackowl.interaction.clarify_gateway import ClarifyGateway
from stackowl.interaction.cost_pause import CostPauseGuard
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import StreamRegistry
from stackowl.providers.base import CompletionResult
from stackowl.providers.cost_tracker import CostTracker
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

USER_ID = 727272

# A low soft budget so a single pre-seeded record crosses it deterministically.
_THRESHOLD_USD = 0.01
# The clarify pause delivers this stem (the guard's question text).
_PAUSE_STEM = "Continue?"


# --- FAKED transport: the Telegram bot HTTP layer (in-process capture) ----------


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


# --- FAKED (THE ONLY AI MOCK): the secretary's scripted provider ----------------


class _ScriptedSecretary:
    """The secretary owl's LLM stand-in: on its turn it calls REAL mixture_of_agents."""

    protocol = "anthropic"
    name = "scripted-secretary"

    def __init__(self) -> None:
        self.moa_out: str = ""
        self.final: str = ""

    async def complete_with_tools(  # noqa: ANN001
        self, *, user_text, system_text, tool_schemas, tool_dispatcher,
        history=None, persistence_check=None, **kwargs,
    ):
        # Call the REAL mixture_of_agents tool. It hits the REAL cost-pause guard,
        # which (over budget + interactive) PARKS on a REAL clarify until the user
        # taps. On Stop the tool returns the cost-budget-stopped record; on
        # Continue it runs the fan-out and returns a synthesized verdict.
        args = {"question": "Which database should we pick for the workload?"}
        self.moa_out = await tool_dispatcher("mixture_of_agents", args)
        self.final = f"MoA result: {self.moa_out[:80]}"
        return (self.final, [{"name": "mixture_of_agents", "args": args, "result": self.moa_out}])

    async def complete(self, *a, **k) -> CompletionResult:  # noqa: ANN002,ANN003
        return CompletionResult(
            content="", input_tokens=1, output_tokens=1, model="scripted",
            provider_name="scripted-secretary", duration_ms=0.0,
        )

    async def stream(self, *a, **k):  # pragma: no cover — not on this path
        if False:
            yield ""


class _RecordingProposer:
    """An MoA proposer provider that RECORDS whether complete() was called."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.protocol = "anthropic"
        self.called = False

    async def complete(self, messages, model: str = "", **kwargs):  # noqa: ANN001, ANN003
        self.called = True
        return CompletionResult(
            content=f"{self.name} says: pick Postgres.",
            input_tokens=5, output_tokens=5, model="scripted-proposer",
            provider_name=self.name, duration_ms=0.0,
        )


class _FakeProviderRegistry:
    """Serves the secretary for routing/dispatch + recording proposers for MoA."""

    def __init__(self, secretary: _ScriptedSecretary, proposers: list[_RecordingProposer]) -> None:
        self._secretary = secretary
        self._proposers = proposers

    def get(self, name: str) -> _ScriptedSecretary:
        return self._secretary

    def get_by_tier(self, tier: str) -> _ScriptedSecretary:
        return self._secretary

    def get_with_cascade(self, tier: str) -> _ScriptedSecretary:
        return self._secretary

    def healthy_distinct(self, limit: int | None = None):  # noqa: ANN201
        roster = list(self._proposers)
        return roster if limit is None else roster[:limit]


@dataclass
class _Env:
    adapter: TelegramChannelAdapter
    bot: _FakeBot
    scanner: GatewayScanner
    backend: AsyncioBackend
    stream_registry: StreamRegistry
    provider: _ScriptedSecretary
    proposers: list[_RecordingProposer]
    gateway: ClarifyGateway
    pump: ClarifyPump


class _FakeDb:
    async def execute(self, sql, params=()):  # noqa: ANN001
        return None

    async def fetch_all(self, sql, params=()):  # noqa: ANN001
        return []


@pytest.fixture(autouse=True)
def _live_io():  # noqa: ANN202
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]
    yield
    TestModeGuard._active = prev  # type: ignore[attr-defined]


def _build() -> _Env:
    adapter = TelegramChannelAdapter(TelegramSettings(allowed_user_ids=frozenset({USER_ID})))
    bot = _FakeBot()
    adapter._bot_app = _FakeBotApp(bot)
    adapter._bot_user_id = 999
    adapter._bot_username = ""

    secretary = _ScriptedSecretary()
    proposers = [_RecordingProposer("alpha"), _RecordingProposer("beta")]
    provider_registry = _FakeProviderRegistry(secretary, proposers)

    gateway = ClarifyGateway()
    gateway.register_adapter("telegram", adapter)

    # REAL pre-seeded CostTracker — the per-turn running total is ALREADY over the
    # low threshold for this trace, so MoA's gate must pause. SAME instance is
    # injected on services.cost_tracker so the guard reads what we seeded.
    cost_tracker = CostTracker(db=_FakeDb(), event_bus=EventBus(), daily_limit_usd=None)  # type: ignore[arg-type]
    guard = CostPauseGuard(
        cost_tracker=cost_tracker,
        clarify_gateway=gateway,
        threshold_usd=_THRESHOLD_USD,
    )

    registry = ToolRegistry.with_defaults()  # REAL mixture_of_agents + clarify

    services = StepServices(
        provider_registry=provider_registry,  # type: ignore[arg-type]
        tool_registry=registry,
        consent_gate=ConsequentialActionGate(),
        stream_registry=StreamRegistry(),
        owl_registry=OwlRegistry.with_default_secretary(),
        clarify_gateway=gateway,
        cost_tracker=cost_tracker,
        cost_pause_guard=guard,
    )
    pump = ClarifyPump(gateway, services.stream_registry)  # type: ignore[arg-type]
    return _Env(
        adapter=adapter, bot=bot, scanner=GatewayScanner(owl_registry=None),
        backend=AsyncioBackend(services=services),
        stream_registry=services.stream_registry,  # type: ignore[arg-type]
        provider=secretary, proposers=proposers, gateway=gateway, pump=pump,
    )


async def _inbound(env: _Env, text: str) -> object:
    update = SimpleNamespace(
        effective_message=SimpleNamespace(text=text),
        effective_user=SimpleNamespace(id=USER_ID),
        effective_chat=SimpleNamespace(id=USER_ID),
    )
    await env.adapter._handle_update(update, None)
    return await env.adapter.receive()


async def _seed_over_budget(env: _Env, trace_id: str) -> None:
    """Record a real cost so this turn's running total is over the soft threshold."""
    tracker = env.backend._services.cost_tracker  # type: ignore[attr-defined]
    assert tracker is not None
    await tracker.record(
        provider_name="p", model="gpt-4o", input_tokens=1000, output_tokens=1000,
        duration_ms=1.0, trace_id=trace_id,
    )
    assert tracker.turn_cost_usd(trace_id) > _THRESHOLD_USD


async def _wait_until(predicate, *, tries: int = 400) -> bool:  # noqa: ANN001
    for _ in range(tries):
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return False


async def _drive_to_pause(env: _Env):  # noqa: ANN202
    """Start the over-budget turn and wait until the REAL cost pause is delivered."""
    msg = await _inbound(env, "Decide our database.")
    decision = env.scanner.scan(msg)
    input_text = decision.stripped_text if decision.stripped_text is not None else msg.text  # type: ignore[attr-defined]
    await _seed_over_budget(env, msg.trace_id)  # type: ignore[attr-defined]

    _writer, reader = env.stream_registry.create(msg.session_id)  # type: ignore[attr-defined]
    state = PipelineState(
        trace_id=msg.trace_id, session_id=msg.session_id, input_text=input_text,  # type: ignore[attr-defined]
        channel=msg.channel, owl_name=decision.target, pipeline_step="start",  # type: ignore[attr-defined]
        interactive=True,
    )
    run_task = asyncio.create_task(env.backend.run(state))
    send_task = asyncio.create_task(env.adapter.send(reader))

    delivered = await _wait_until(
        lambda: any(_PAUSE_STEM in m["text"] for m in env.bot.messages)
    )
    return msg, run_task, send_task, delivered


async def test_cost_pause_stop_aborts_the_expensive_op() -> None:
    """SCENARIO A — over budget → REAL 'Continue?' clarify → tap STOP → MoA ABORTS."""
    env = _build()
    msg, run_task, send_task, delivered = await _drive_to_pause(env)

    # BUSINESS OUTCOME 1 — the cost-pause question reached the USER's chat AND the
    # turn is genuinely SUSPENDED while it waits.
    assert delivered, (
        "FAIL: the cost-pause 'Continue?' was never delivered. The pause is unwired. "
        f"Outbound: {[m['text'] for m in env.bot.messages]!r}"
    )
    assert not run_task.done(), "FAIL: the turn did not suspend on the cost pause."
    # No proposer has run yet — the gate is BEFORE the fan-out.
    assert not any(p.called for p in env.proposers), (
        "FAIL: a proposer ran BEFORE the user answered — the gate is after the fan-out."
    )

    # The user taps STOP — a REAL inbound reply routed through the REAL pump.
    answer_msg = await _inbound(env, "Stop")
    answer_decision = env.scanner.scan(answer_msg)
    consumed, _rw = await env.pump.resolve_or_rewrite(
        session_id=answer_msg.session_id,  # type: ignore[attr-defined]
        channel=answer_msg.channel,  # type: ignore[attr-defined]
        route=answer_decision.route,
        target=answer_decision.target,
        input_text=answer_msg.text,  # type: ignore[attr-defined]
    )
    assert consumed, "FAIL: the pump did not resume the parked turn on the tap."

    await asyncio.wait_for(run_task, timeout=5.0)
    await asyncio.wait_for(send_task, timeout=5.0)
    env.stream_registry.remove(msg.session_id)  # type: ignore[attr-defined]

    # BUSINESS OUTCOME 2 — the MoA op ABORTED from REAL effects: NO proposer's
    # complete() ran (the fan-out never started) AND the tool result is the
    # cost-budget-stopped record.
    assert not any(p.called for p in env.proposers), (
        "FAIL: a proposer ran AFTER the user chose Stop — the expensive op was NOT "
        f"aborted. proposers={[(p.name, p.called) for p in env.proposers]}"
    )
    assert "cost_budget_stopped" in env.provider.moa_out, (
        f"FAIL: MoA did not return the cost-budget-stopped record. Got: {env.provider.moa_out!r}"
    )


async def test_cost_pause_continue_proceeds_with_the_op() -> None:
    """SCENARIO B — over budget → REAL 'Continue?' clarify → tap CONTINUE → fan-out RUNS."""
    env = _build()
    msg, run_task, send_task, delivered = await _drive_to_pause(env)

    assert delivered, "FAIL: the cost-pause 'Continue?' was never delivered."
    assert not run_task.done(), "FAIL: the turn did not suspend on the cost pause."

    # The user taps CONTINUE — a REAL inbound reply routed through the REAL pump.
    answer_msg = await _inbound(env, "Continue")
    answer_decision = env.scanner.scan(answer_msg)
    consumed, _rw = await env.pump.resolve_or_rewrite(
        session_id=answer_msg.session_id,  # type: ignore[attr-defined]
        channel=answer_msg.channel,  # type: ignore[attr-defined]
        route=answer_decision.route,
        target=answer_decision.target,
        input_text=answer_msg.text,  # type: ignore[attr-defined]
    )
    assert consumed, "FAIL: the pump did not resume the parked turn on the tap."

    await asyncio.wait_for(run_task, timeout=5.0)
    await asyncio.wait_for(send_task, timeout=5.0)
    env.stream_registry.remove(msg.session_id)  # type: ignore[attr-defined]

    # BUSINESS OUTCOME — the fan-out RAN: the proposers WERE consulted (real effect)
    # and MoA did NOT return the cost-budget-stopped record.
    assert any(p.called for p in env.proposers), (
        "FAIL: no proposer ran after the user chose Continue — the op did NOT proceed."
    )
    assert "cost_budget_stopped" not in env.provider.moa_out, (
        f"FAIL: MoA returned the stopped record despite Continue. Got: {env.provider.moa_out!r}"
    )
