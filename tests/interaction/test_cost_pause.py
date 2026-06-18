"""Unit tests for CostPauseGuard + CostTracker.turn_cost_usd (E8-S0cost).

Covers the soft per-turn cost-pause guard's decision matrix and the CostTracker's
bounded per-trace running total:

* over-threshold + interactive → ASKS (the fake clarify gateway records the ask);
* a "Stop" answer → gate returns False; a "Continue" answer → True AND a second
  gate() on the SAME trace does NOT re-ask (asked-once-per-turn);
* under threshold / non-interactive / threshold=None → True, NO ask;
* ``turn_cost_usd`` sums per trace and FIFO-evicts past the bound;
* any gateway error → fail-OPEN (True), never wedges the turn.

The clarify gateway is a fake (records ask args + returns a scripted answer); the
CostTracker is REAL but backed by a fake DbPool so ``turn_cost_usd`` exercises the
genuine in-memory running total without touching SQLite.
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.events.bus import EventBus
from stackowl.interaction.clarify_gateway import OUTCOME_ANSWERED, OUTCOME_TIMED_OUT
from stackowl.interaction.cost_pause import CostPauseGuard
from stackowl.providers.cost_tracker import (  # type: ignore[attr-defined]
    _MAX_TRACKED_TURNS,
    CostTracker,
)

# --- fakes -------------------------------------------------------------------


class _FakeDb:
    """A no-op DbPool stand-in: swallows INSERTs, returns no rows for SELECTs."""

    async def execute(self, sql: str, params: Any = ()) -> None:  # noqa: ANN401, ARG002
        return None

    async def fetch_all(self, sql: str, params: Any = ()) -> list[dict[str, Any]]:  # noqa: ANN401, ARG002
        return []


class _FakeClarifyGateway:
    """Records each ask + returns a scripted (answer, outcome) on wait_for_answer."""

    def __init__(self, answer: str | None, outcome: str = OUTCOME_ANSWERED) -> None:
        self.asks: list[dict[str, Any]] = []
        self._answer = answer
        self._outcome = outcome
        self._next_id = 0

    async def ask(self, session_id: str, channel: str, question: str, **kwargs: Any) -> str:  # noqa: ANN401
        self._next_id += 1
        clarify_id = f"cid-{self._next_id}"
        self.asks.append(
            {
                "session_id": session_id,
                "channel": channel,
                "question": question,
                "choices": kwargs.get("choices"),
                "blocking": kwargs.get("blocking"),
                "clarify_id": clarify_id,
            }
        )
        return clarify_id

    async def wait_for_answer(self, clarify_id: str, timeout: float) -> tuple[str | None, str]:  # noqa: ARG002
        return (self._answer, self._outcome)


class _ExplodingGateway:
    """A clarify gateway whose ask() raises — exercises the fail-OPEN path."""

    def __init__(self) -> None:
        self.asks: list[dict[str, Any]] = []

    async def ask(self, *args: Any, **kwargs: Any) -> str:  # noqa: ANN401, ARG002
        raise RuntimeError("gateway boom")

    async def wait_for_answer(self, clarify_id: str, timeout: float) -> tuple[str | None, str]:  # noqa: ANN401, ARG002
        return (None, OUTCOME_TIMED_OUT)


def _tracker() -> CostTracker:
    return CostTracker(db=_FakeDb(), event_bus=EventBus(), daily_limit_usd=None)  # type: ignore[arg-type]


def _guard(
    *,
    tracker: CostTracker,
    gateway: Any,  # noqa: ANN401
    threshold: float | None,
) -> CostPauseGuard:
    return CostPauseGuard(
        cost_tracker=tracker,
        clarify_gateway=gateway,
        threshold_usd=threshold,
    )


async def _seed_turn_cost(tracker: CostTracker, trace_id: str, cost: float) -> None:
    """Record a synthetic call so turn_cost_usd(trace_id) >= cost.

    Uses a model the pricing table prices > 0; we top up via repeated records
    until the running total reaches the target, so the test is independent of the
    exact per-token price.
    """
    # Record once and read what it produced, then scale by recording again.
    for _ in range(50):
        await tracker.record(
            provider_name="p", model="gpt-4o", input_tokens=1000,
            output_tokens=1000, duration_ms=1.0, trace_id=trace_id,
        )
        if tracker.turn_cost_usd(trace_id) >= cost:
            return


# --- turn_cost_usd: summing + bounded eviction -------------------------------


async def test_turn_cost_sums_per_trace_and_is_isolated() -> None:
    tracker = _tracker()
    await _seed_turn_cost(tracker, "trace-A", 0.10)
    a_after_one = tracker.turn_cost_usd("trace-A")
    # A second trace is independent.
    await tracker.record(
        provider_name="p", model="gpt-4o", input_tokens=10, output_tokens=10,
        duration_ms=1.0, trace_id="trace-B",
    )
    assert tracker.turn_cost_usd("trace-A") == pytest.approx(a_after_one)
    assert tracker.turn_cost_usd("trace-B") > 0.0
    assert tracker.turn_cost_usd("trace-B") < tracker.turn_cost_usd("trace-A")
    # Unknown / empty trace → 0.0 (never raises).
    assert tracker.turn_cost_usd("nope") == 0.0
    assert tracker.turn_cost_usd("") == 0.0


async def test_turn_cost_bounded_eviction() -> None:
    tracker = _tracker()
    # Fill past the cap with distinct traces; the FIRST inserted must be evicted.
    for i in range(_MAX_TRACKED_TURNS + 5):
        await tracker.record(
            provider_name="p", model="gpt-4o", input_tokens=1, output_tokens=1,
            duration_ms=1.0, trace_id=f"t-{i}",
        )
    # The oldest 5 traces were FIFO-evicted → read back as 0.0.
    assert tracker.turn_cost_usd("t-0") == 0.0
    assert tracker.turn_cost_usd("t-4") == 0.0
    # A recent trace is still tracked.
    assert tracker.turn_cost_usd(f"t-{_MAX_TRACKED_TURNS + 4}") > 0.0


# --- gate: no-pause fast paths -----------------------------------------------


async def test_gate_threshold_none_never_asks() -> None:
    tracker = _tracker()
    await _seed_turn_cost(tracker, "t", 1.00)
    gw = _FakeClarifyGateway(answer="Stop")
    guard = _guard(tracker=tracker, gateway=gw, threshold=None)
    assert await guard.gate(trace_id="t", session_id="s", channel="telegram", interactive=True) is True
    assert gw.asks == []


async def test_gate_under_threshold_never_asks() -> None:
    tracker = _tracker()
    # No cost recorded → turn_cost is 0, threshold is high.
    gw = _FakeClarifyGateway(answer="Stop")
    guard = _guard(tracker=tracker, gateway=gw, threshold=0.50)
    assert await guard.gate(trace_id="t", session_id="s", channel="telegram", interactive=True) is True
    assert gw.asks == []


async def test_gate_non_interactive_never_asks() -> None:
    tracker = _tracker()
    await _seed_turn_cost(tracker, "t", 1.00)
    gw = _FakeClarifyGateway(answer="Stop")
    guard = _guard(tracker=tracker, gateway=gw, threshold=0.10)
    assert await guard.gate(trace_id="t", session_id="s", channel="telegram", interactive=False) is True
    assert gw.asks == []


# --- gate: the pause itself --------------------------------------------------


async def test_gate_over_threshold_interactive_asks_and_stop_returns_false() -> None:
    tracker = _tracker()
    await _seed_turn_cost(tracker, "t", 0.10)
    gw = _FakeClarifyGateway(answer="Stop")
    guard = _guard(tracker=tracker, gateway=gw, threshold=0.10)
    result = await guard.gate(trace_id="t", session_id="s", channel="telegram", interactive=True)
    assert result is False
    # It ASKED — with the Continue/Stop choices, blocking, on the right channel.
    assert len(gw.asks) == 1
    ask = gw.asks[0]
    assert ask["choices"] == ("Continue", "Stop")
    assert ask["blocking"] is True
    assert ask["session_id"] == "s"
    assert ask["channel"] == "telegram"
    assert "$" in ask["question"] and "Continue?" in ask["question"]


async def test_gate_continue_returns_true_and_does_not_reask_same_trace() -> None:
    tracker = _tracker()
    await _seed_turn_cost(tracker, "t", 0.10)
    gw = _FakeClarifyGateway(answer="Continue")
    guard = _guard(tracker=tracker, gateway=gw, threshold=0.10)
    first = await guard.gate(trace_id="t", session_id="s", channel="telegram", interactive=True)
    assert first is True
    assert len(gw.asks) == 1
    # Second expensive op in the SAME turn → no re-ask (asked-once), still True.
    second = await guard.gate(trace_id="t", session_id="s", channel="telegram", interactive=True)
    assert second is True
    assert len(gw.asks) == 1


async def test_gate_timeout_no_answer_fails_open() -> None:
    tracker = _tracker()
    await _seed_turn_cost(tracker, "t", 0.10)
    gw = _FakeClarifyGateway(answer=None, outcome=OUTCOME_TIMED_OUT)
    guard = _guard(tracker=tracker, gateway=gw, threshold=0.10)
    assert await guard.gate(trace_id="t", session_id="s", channel="telegram", interactive=True) is True
    assert len(gw.asks) == 1


# --- self-healing: fail-OPEN -------------------------------------------------


async def test_gate_no_gateway_fails_open() -> None:
    tracker = _tracker()
    await _seed_turn_cost(tracker, "t", 0.10)
    guard = _guard(tracker=tracker, gateway=None, threshold=0.10)
    assert await guard.gate(trace_id="t", session_id="s", channel="telegram", interactive=True) is True


async def test_gate_missing_channel_fails_open() -> None:
    tracker = _tracker()
    await _seed_turn_cost(tracker, "t", 0.10)
    gw = _FakeClarifyGateway(answer="Stop")
    guard = _guard(tracker=tracker, gateway=gw, threshold=0.10)
    # No channel → cannot ask → fail-OPEN, no ask recorded.
    assert await guard.gate(trace_id="t", session_id="s", channel="", interactive=True) is True
    assert gw.asks == []


async def test_gate_gateway_error_fails_open() -> None:
    tracker = _tracker()
    await _seed_turn_cost(tracker, "t", 0.10)
    gw = _ExplodingGateway()
    guard = _guard(tracker=tracker, gateway=gw, threshold=0.10)
    # ask() raises inside the guard → it logs (B5) + fails OPEN (True), never raises.
    assert await guard.gate(trace_id="t", session_id="s", channel="telegram", interactive=True) is True
