"""Story 3.2 + 3.3 tests — CircuitBreaker, RateLimiter, cascade, CostTracker."""

from __future__ import annotations

import asyncio
import datetime
from typing import Any

import pytest

from stackowl.db.pool import DbPool
from stackowl.events.bus import EventBus
from stackowl.exceptions import (
    AllProvidersUnavailableError,
    CircuitOpenError,
    ProviderError,
)
from stackowl.providers.circuit_breaker import CircuitBreaker, CircuitState
from stackowl.providers.cost_tracker import CostTracker
from stackowl.providers.mock_provider import MockProvider
from stackowl.providers.rate_limiter import RateLimiter
from stackowl.providers.registry import ProviderRegistry

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


class FakeClock:
    """Monotonic clock that advances on sleep; async_sleep yields once then returns."""

    def __init__(self) -> None:
        self._t = 0.0

    def monotonic(self) -> float:
        return self._t

    def advance(self, seconds: float) -> None:
        self._t += seconds

    async def async_sleep(self, seconds: float) -> None:
        self._t += seconds
        await asyncio.sleep(0)


async def _ok() -> str:
    return "ok"


async def _boom() -> str:
    raise RuntimeError("boom")


# ---------------------------------------------------------------------------
# CircuitBreaker
# ---------------------------------------------------------------------------


async def test_circuit_initial_state_is_closed() -> None:
    cb = CircuitBreaker("p", clock=FakeClock())
    assert cb.state is CircuitState.CLOSED
    assert cb.retry_after_seconds == 0.0


async def test_circuit_closed_runs_coro_and_returns_value() -> None:
    cb = CircuitBreaker("p", clock=FakeClock())
    result = await cb.call(_ok())
    assert result == "ok"
    assert cb.state is CircuitState.CLOSED


async def test_circuit_opens_after_threshold_failures() -> None:
    cb = CircuitBreaker("p", failure_threshold=3, window_seconds=60, clock=FakeClock())
    for _ in range(3):
        with pytest.raises(RuntimeError):
            await cb.call(_boom())
    assert cb.state is CircuitState.OPEN


async def test_open_circuit_short_circuits_with_circuit_open_error() -> None:
    clock = FakeClock()
    cb = CircuitBreaker("p", failure_threshold=2, window_seconds=60, half_open_seconds=30, clock=clock)
    for _ in range(2):
        with pytest.raises(RuntimeError):
            await cb.call(_boom())
    assert cb.state is CircuitState.OPEN

    with pytest.raises(CircuitOpenError) as info:
        await cb.call(_ok())
    assert info.value.provider_name == "p"
    assert 0.0 < info.value.retry_after_seconds <= 30.0


async def test_circuit_transitions_to_half_open_after_window() -> None:
    clock = FakeClock()
    cb = CircuitBreaker("p", failure_threshold=2, window_seconds=60, half_open_seconds=30, clock=clock)
    for _ in range(2):
        with pytest.raises(RuntimeError):
            await cb.call(_boom())
    assert cb.state is CircuitState.OPEN

    clock.advance(31.0)
    assert cb.state is CircuitState.HALF_OPEN
    assert cb.retry_after_seconds == 0.0


async def test_half_open_success_closes_circuit() -> None:
    clock = FakeClock()
    cb = CircuitBreaker("p", failure_threshold=2, window_seconds=60, half_open_seconds=30, clock=clock)
    for _ in range(2):
        with pytest.raises(RuntimeError):
            await cb.call(_boom())
    clock.advance(31.0)
    assert cb.state is CircuitState.HALF_OPEN

    result = await cb.call(_ok())
    assert result == "ok"
    assert cb.state is CircuitState.CLOSED


async def test_half_open_failure_reopens_circuit() -> None:
    clock = FakeClock()
    cb = CircuitBreaker("p", failure_threshold=2, window_seconds=60, half_open_seconds=30, clock=clock)
    for _ in range(2):
        with pytest.raises(RuntimeError):
            await cb.call(_boom())
    clock.advance(31.0)
    assert cb.state is CircuitState.HALF_OPEN

    with pytest.raises(RuntimeError):
        await cb.call(_boom())
    assert cb.state is CircuitState.OPEN


# ---------------------------------------------------------------------------
# RateLimiter
# ---------------------------------------------------------------------------


async def test_rate_limiter_is_noop_when_rpm_none() -> None:
    rl = RateLimiter.from_rpm("p", None, clock=FakeClock())
    assert rl.is_noop is True
    await rl.acquire()  # must not raise / block


async def test_rate_limiter_acquire_immediate_when_tokens_available() -> None:
    clock = FakeClock()
    rl = RateLimiter("p", capacity=5, refill_rate=1.0, clock=clock)
    start = clock.monotonic()
    await rl.acquire(1)
    assert clock.monotonic() == start  # no sleeping


async def test_rate_limiter_blocks_when_tokens_exhausted() -> None:
    clock = FakeClock()
    rl = RateLimiter("p", capacity=2, refill_rate=1.0, clock=clock)
    await rl.acquire(2)  # drains bucket
    start = clock.monotonic()
    await rl.acquire(1)  # must wait ~1s for refill
    assert clock.monotonic() - start >= 0.99


async def test_rate_limiter_from_rpm_creates_proper_capacity() -> None:
    clock = FakeClock()
    rl = RateLimiter.from_rpm("p", 60, clock=clock)  # 60 rpm = 1 token/sec
    assert rl.is_noop is False
    # initial bucket is full; we can consume capacity (60) immediately
    await rl.acquire(60)
    start = clock.monotonic()
    await rl.acquire(1)
    assert clock.monotonic() - start >= 0.99


# ---------------------------------------------------------------------------
# ProviderRegistry cascade
# ---------------------------------------------------------------------------


def _build_registry_with_mocks() -> ProviderRegistry:
    reg = ProviderRegistry(clock=FakeClock())
    reg.register_mock("fast-mock", MockProvider("fast-mock"), tier="fast")
    reg.register_mock("std-mock", MockProvider("std-mock"), tier="standard")
    reg.register_mock("pow-mock", MockProvider("pow-mock"), tier="powerful")
    reg.register_mock("local-mock", MockProvider("local-mock"), tier="local")
    return reg


async def test_cascade_returns_preferred_when_all_closed() -> None:
    reg = _build_registry_with_mocks()
    chosen = reg.get_with_cascade("fast")
    assert chosen.name == "fast-mock"


async def test_cascade_skips_open_provider_and_falls_to_next_tier() -> None:
    reg = _build_registry_with_mocks()
    breaker = reg.get_circuit_breaker("fast-mock")
    assert breaker is not None
    for _ in range(3):
        with pytest.raises(RuntimeError):
            await breaker.call(_boom())
    assert breaker.state is CircuitState.OPEN

    chosen = reg.get_with_cascade("fast")
    assert chosen.name == "std-mock"


async def test_cascade_raises_when_all_providers_open() -> None:
    reg = _build_registry_with_mocks()
    for name in ("fast-mock", "std-mock", "pow-mock", "local-mock"):
        breaker = reg.get_circuit_breaker(name)
        assert breaker is not None
        for _ in range(3):
            with pytest.raises(RuntimeError):
                await breaker.call(_boom())
        assert breaker.state is CircuitState.OPEN

    with pytest.raises(AllProvidersUnavailableError) as info:
        reg.get_with_cascade("fast")
    assert len(info.value.details) == 4


async def test_cascade_unknown_tier_falls_back_to_full_order() -> None:
    reg = _build_registry_with_mocks()
    chosen = reg.get_with_cascade("nonexistent-tier")
    # fast comes first in default order
    assert chosen.name == "fast-mock"


# ---------------------------------------------------------------------------
# CostTracker
# ---------------------------------------------------------------------------


def _make_tracker(db: DbPool, limit: float | None = None) -> tuple[CostTracker, list[tuple[str, Any]]]:
    bus = EventBus()
    events: list[tuple[str, Any]] = []
    bus.subscribe("budget_80pct_alert", lambda p: events.append(("80", p)))
    bus.subscribe("budget_exceeded", lambda p: events.append(("exceeded", p)))
    tracker = CostTracker(db=db, event_bus=bus, daily_limit_usd=limit)
    return tracker, events


async def test_cost_tracker_record_persists(tmp_db: DbPool) -> None:
    tracker, _events = _make_tracker(tmp_db)
    rec = await tracker.record(
        provider_name="anth",
        model="claude-opus-4-7",
        input_tokens=1000,
        output_tokens=500,
        duration_ms=120.0,
        trace_id="t-1",
    )
    expected = (1000 * 15.0 + 500 * 75.0) / 1_000_000.0
    assert rec.cost_usd == pytest.approx(expected)

    rows = await tmp_db.fetch_all(
        "SELECT provider_name, model, input_tokens, output_tokens, cost_usd, trace_id FROM cost_records"
    )
    assert len(rows) == 1
    assert rows[0]["provider_name"] == "anth"
    assert rows[0]["model"] == "claude-opus-4-7"
    assert rows[0]["cost_usd"] == pytest.approx(expected)
    assert rows[0]["trace_id"] == "t-1"


async def test_cost_tracker_unknown_model_falls_back_to_local_default(tmp_db: DbPool) -> None:
    tracker, _events = _make_tracker(tmp_db)
    rec = await tracker.record(
        provider_name="ollama",
        model="some-unknown-model",
        input_tokens=1_000_000,
        output_tokens=500_000,
        duration_ms=10.0,
    )
    assert rec.cost_usd == 0.0


async def test_cost_tracker_daily_total_aggregates(tmp_db: DbPool) -> None:
    tracker, _events = _make_tracker(tmp_db)
    today = datetime.datetime.now(tz=datetime.UTC).date().isoformat()
    await tracker.record("anth", "claude-opus-4-7", 1000, 500, 1.0)
    await tracker.record("anth", "claude-sonnet-4-6", 2000, 1000, 1.0)
    await tracker.record("openai", "gpt-4o-mini", 5000, 1000, 1.0)
    summary = await tracker.daily_total(today)
    assert summary.call_count == 3
    assert summary.total_usd > 0
    assert "anth" in summary.by_provider
    assert "openai" in summary.by_provider
    assert "claude-opus-4-7" in summary.by_model


async def test_cost_tracker_emits_80pct_alert(tmp_db: DbPool) -> None:
    # claude-opus-4-7: 1k in + 1k out = (15+75)/1000 = 0.090
    # set limit so that one call = 90% of limit -> 80pct alert
    tracker, events = _make_tracker(tmp_db, limit=0.100)
    await tracker.record("anth", "claude-opus-4-7", 1000, 1000, 1.0)
    kinds = [e[0] for e in events]
    assert "80" in kinds
    assert "exceeded" not in kinds
    payload = next(p for k, p in events if k == "80")
    assert payload["limit_usd"] == 0.100
    assert payload["current_usd"] > 0


async def test_cost_tracker_emits_exceeded_and_blocks_next(tmp_db: DbPool) -> None:
    tracker, events = _make_tracker(tmp_db, limit=0.050)
    # single call: 0.090 > 0.050 → exceeded
    await tracker.record("anth", "claude-opus-4-7", 1000, 1000, 1.0)
    kinds = [e[0] for e in events]
    assert "exceeded" in kinds

    with pytest.raises(ProviderError):
        await tracker.record("anth", "claude-opus-4-7", 100, 100, 1.0)


async def test_cost_tracker_update_limit_hot_reload(tmp_db: DbPool) -> None:
    tracker, events = _make_tracker(tmp_db, limit=0.050)
    await tracker.record("anth", "claude-opus-4-7", 1000, 1000, 1.0)
    assert any(k == "exceeded" for k, _ in events)

    # Raise the limit; subsequent record() must succeed (no more blocking).
    tracker.update_limit(daily_limit_usd=1000.0)
    rec = await tracker.record("anth", "claude-opus-4-7", 100, 100, 1.0)
    assert rec.cost_usd > 0


async def test_cost_tracker_no_limit_never_blocks(tmp_db: DbPool) -> None:
    tracker, events = _make_tracker(tmp_db, limit=None)
    for _ in range(5):
        await tracker.record("anth", "claude-opus-4-7", 1000, 1000, 1.0)
    assert events == []
