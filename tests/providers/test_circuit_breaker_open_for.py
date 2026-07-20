"""Tests for CircuitBreaker.open_for — explicit-duration quota cooldown."""

from __future__ import annotations

import pytest

from stackowl.infra.clock import Clock
from stackowl.providers.circuit_breaker import CircuitBreaker, CircuitState


class _FakeClock(Clock):
    def __init__(self) -> None:
        self._t = 0.0

    def monotonic(self) -> float:
        return self._t

    async def async_sleep(self, seconds: float) -> None:  # pragma: no cover - unused here
        self._t += seconds

    def advance(self, seconds: float) -> None:
        self._t += seconds


@pytest.mark.asyncio
async def test_open_for_forces_open_with_exact_duration() -> None:
    clock = _FakeClock()
    breaker = CircuitBreaker(provider_name="p", clock=clock)

    await breaker.open_for(3600.0)

    assert breaker.state is CircuitState.OPEN
    assert breaker.retry_after_seconds == pytest.approx(3600.0)


@pytest.mark.asyncio
async def test_open_for_promotes_to_half_open_after_duration_elapses() -> None:
    clock = _FakeClock()
    breaker = CircuitBreaker(provider_name="p", clock=clock)

    await breaker.open_for(100.0)
    clock.advance(101.0)

    assert breaker.state is CircuitState.HALF_OPEN


@pytest.mark.asyncio
async def test_open_for_overrides_any_prior_failure_count() -> None:
    clock = _FakeClock()
    breaker = CircuitBreaker(provider_name="p", failure_threshold=3, clock=clock)
    await breaker.record(ok=False)  # one failure, not yet OPEN

    await breaker.open_for(60.0)

    assert breaker.state is CircuitState.OPEN
    assert breaker.retry_after_seconds == pytest.approx(60.0)
