"""FUNCTIONAL proof (E8-S0cost) — REAL recorded completion cost fires the pause.

The mechanism test (``test_e8_s0cost_cost_pause_telegram_smoke``) PRE-SEEDS the
CostTracker so the turn is already over budget. THIS test proves the END-TO-END
the seeding stood in for: a turn's REAL main-pipeline completion cost — recorded
by the now-instrumented PROVIDER (the single recording site, ``ModelProvider.
_record_cost``, reading ``trace_id`` off ``TraceContext``) — crosses a LOW
``per_turn_pause_usd`` so the SAME turn's next expensive op (``mixture_of_agents``)
triggers the cost-pause "Continue?" to the user.

NO pre-seed: the over-budget condition is produced ONLY by a genuine
``provider.complete`` call going through ``_record_cost`` into the REAL
``CostTracker.turn_cost_usd(trace_id)``. The provider is a REAL ``ModelProvider``
subclass given the shared tracker via ``ProviderRegistry.set_cost_tracker`` (exactly
the orchestrator wiring), so the recording path is the production path — not a fake.

FAIL if instrumentation is missing: if the provider did NOT record, the turn total
stays 0, the guard never crosses the threshold, no "Continue?" is delivered, and the
delivered-assertion fails — which is precisely the toothless state E8-S0cost fixes.
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.config.test_mode import TestModeGuard
from stackowl.events.bus import EventBus
from stackowl.infra.trace import TraceContext
from stackowl.interaction.cost_pause import CostPauseGuard, gate_or_continue
from stackowl.pipeline.services import StepServices, get_services, reset_services, set_services
from stackowl.providers.base import CompletionResult, Message, ModelProvider
from stackowl.providers.cost_tracker import CostTracker
from stackowl.providers.registry import ProviderRegistry

# A low soft budget; ONE real recorded completion with a large token count crosses it.
_THRESHOLD_USD = 0.01
_PAUSE_STEM = "Continue?"


@pytest.fixture(autouse=True)
def _live_io():  # noqa: ANN202
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]
    yield
    TestModeGuard._active = prev  # type: ignore[attr-defined]


class _FakeDb:
    async def execute(self, sql: str, params: Any = ()) -> None:  # noqa: ANN401, ARG002
        return None

    async def fetch_all(self, sql: str, params: Any = ()) -> list[dict[str, Any]]:  # noqa: ANN401, ARG002
        return []


class _RecordingProvider(ModelProvider):
    """A REAL ModelProvider subclass that records via the inherited _record_cost.

    Its ``complete`` mirrors the real providers: build a CompletionResult, then call
    ``self._record_cost(...)`` (the single recording site) with a LARGE token count
    so one call's priced cost crosses the low soft threshold — exactly how a real
    expensive main completion would.
    """

    def __init__(self, name: str, *, in_tokens: int, out_tokens: int) -> None:
        self._name = name
        self._in = in_tokens
        self._out = out_tokens

    @property
    def name(self) -> str:
        return self._name

    @property
    def protocol(self) -> str:  # type: ignore[override]
        return "anthropic"

    async def complete(self, messages: list[Message], model: str, **kwargs: object) -> CompletionResult:
        result = CompletionResult(
            content="ok", input_tokens=self._in, output_tokens=self._out,
            model="gpt-4o", provider_name=self._name, duration_ms=1.0,
        )
        # SINGLE recording site — the production path that feeds turn_cost_usd.
        await self._record_cost(
            model=result.model, input_tokens=result.input_tokens,
            output_tokens=result.output_tokens, duration_ms=result.duration_ms,
        )
        return result

    async def stream(self, messages: list[Message], model: str, **kwargs: object):  # noqa: ANN201
        if False:  # pragma: no cover
            yield ""


class _RecordingClarifyGateway:
    """Records each ask + returns a scripted answer (the user's tap)."""

    def __init__(self, answer: str) -> None:
        self.asks: list[dict[str, Any]] = []
        self._answer = answer

    async def ask(self, session_id: str, channel: str, question: str, **kwargs: Any) -> str:  # noqa: ANN401
        self.asks.append({"question": question, "choices": kwargs.get("choices")})
        return f"cid-{len(self.asks)}"

    async def wait_for_answer(self, clarify_id: str, timeout: float) -> tuple[str | None, str]:  # noqa: ARG002
        return (self._answer, "answered")


def _build(answer: str) -> tuple[ProviderRegistry, CostTracker, CostPauseGuard, _RecordingClarifyGateway]:
    registry = ProviderRegistry()
    # One real recording provider with a LARGE token count → its single completion
    # crosses the low threshold once recorded.
    provider = _RecordingProvider("main", in_tokens=2000, out_tokens=2000)
    registry.register_mock("main", provider, tier="powerful")
    tracker = CostTracker(db=_FakeDb(), event_bus=EventBus(), daily_limit_usd=None)  # type: ignore[arg-type]
    # Orchestrator wiring: inject the ONE shared tracker into the registry+providers.
    registry.set_cost_tracker(tracker)
    gateway = _RecordingClarifyGateway(answer)
    guard = CostPauseGuard(
        cost_tracker=tracker, clarify_gateway=gateway, threshold_usd=_THRESHOLD_USD,  # type: ignore[arg-type]
    )
    return registry, tracker, guard, gateway


async def test_real_completion_cost_crosses_threshold_and_pause_fires() -> None:
    """REAL recorded completion → turn total crosses → the next op's gate ASKS."""
    registry, tracker, guard, gateway = _build(answer="Stop")
    trace_id = "trace-func-1"

    # Run the "main completion" UNDER the turn's trace context, exactly like the
    # pipeline — _record_cost reads trace_id off TraceContext, so the spend folds
    # into THIS turn's running total. NO pre-seed: the total starts at zero.
    assert tracker.turn_cost_usd(trace_id) == 0.0, "precondition: nothing recorded yet"
    token = TraceContext.start(
        session_id="s", trace_id=trace_id, interactive=True, channel="telegram",
    )
    try:
        provider = registry.get("main")
        await provider.complete([Message(role="user", content="big expensive prompt")], model="")

        # PROOF 1 — the REAL completion's cost was recorded and crossed the budget,
        # with NO pre-seed: the turn total is now > 0 AND over the soft threshold.
        crossed = tracker.turn_cost_usd(trace_id)
        assert crossed > 0.0, "FAIL: provider did not record cost (instrumentation missing)."
        assert crossed >= _THRESHOLD_USD, (
            f"FAIL: recorded cost ${crossed:.6f} did not cross ${_THRESHOLD_USD}."
        )

        # PROOF 2 — the SAME turn's next expensive op gates: the guard ASKS the user
        # (the shared gate helper both tools use), then maps the 'Stop' tap to abort.
        svc_token = set_services(
            StepServices(provider_registry=registry, cost_pause_guard=guard),  # type: ignore[arg-type]
        )
        try:
            proceed = await gate_or_continue(get_services(), action="fan-out")
        finally:
            reset_services(svc_token)
    finally:
        TraceContext.reset(token)

    # The pause FIRED off the REAL recorded cost: a "Continue?" was delivered with the
    # Continue/Stop choices, and the user's 'Stop' aborted the expensive op.
    assert len(gateway.asks) == 1, (
        f"FAIL: the cost-pause never asked — pause is toothless. asks={gateway.asks!r}"
    )
    assert _PAUSE_STEM in gateway.asks[0]["question"]
    assert gateway.asks[0]["choices"] == ("Continue", "Stop")
    assert proceed is False, "FAIL: 'Stop' tap did not abort the expensive op."


async def test_continue_tap_proceeds_after_real_recorded_cost() -> None:
    """Same REAL-cost crossing → a 'Continue' tap lets the expensive op proceed."""
    registry, tracker, guard, gateway = _build(answer="Continue")
    trace_id = "trace-func-2"
    token = TraceContext.start(
        session_id="s", trace_id=trace_id, interactive=True, channel="telegram",
    )
    try:
        await registry.get("main").complete([Message(role="user", content="x")], model="")
        assert tracker.turn_cost_usd(trace_id) >= _THRESHOLD_USD
        svc_token = set_services(
            StepServices(provider_registry=registry, cost_pause_guard=guard),  # type: ignore[arg-type]
        )
        try:
            proceed = await gate_or_continue(get_services(), action="fan-out")
        finally:
            reset_services(svc_token)
    finally:
        TraceContext.reset(token)

    assert len(gateway.asks) == 1, "FAIL: the pause should have asked on a crossed budget."
    assert proceed is True, "FAIL: a 'Continue' tap must let the op proceed."


async def test_under_threshold_real_cost_does_not_pause() -> None:
    """A SMALL real recorded completion stays under budget → NO pause (control)."""
    registry = ProviderRegistry()
    # Tiny token count → priced well under the threshold.
    registry.register_mock("main", _RecordingProvider("main", in_tokens=1, out_tokens=1), tier="powerful")
    tracker = CostTracker(db=_FakeDb(), event_bus=EventBus(), daily_limit_usd=None)  # type: ignore[arg-type]
    registry.set_cost_tracker(tracker)
    gateway = _RecordingClarifyGateway(answer="Stop")
    guard = CostPauseGuard(
        cost_tracker=tracker, clarify_gateway=gateway, threshold_usd=_THRESHOLD_USD,  # type: ignore[arg-type]
    )
    trace_id = "trace-func-3"
    token = TraceContext.start(
        session_id="s", trace_id=trace_id, interactive=True, channel="telegram",
    )
    try:
        await registry.get("main").complete([Message(role="user", content="x")], model="")
        assert 0.0 < tracker.turn_cost_usd(trace_id) < _THRESHOLD_USD, (
            "precondition: a tiny real cost recorded, but under budget"
        )
        svc_token = set_services(
            StepServices(provider_registry=registry, cost_pause_guard=guard),  # type: ignore[arg-type]
        )
        try:
            proceed = await gate_or_continue(get_services(), action="fan-out")
        finally:
            reset_services(svc_token)
    finally:
        TraceContext.reset(token)

    assert gateway.asks == [], "FAIL: paused while under budget — the gate is too eager."
    assert proceed is True
