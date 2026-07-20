"""Tests for CircuitBreaker.open_for — explicit-duration quota cooldown."""

from __future__ import annotations

import math

import pytest

from stackowl.infra.clock import Clock
from stackowl.providers.circuit_breaker import (
    _HALF_OPEN_BACKOFF_CAP_SECONDS,
    CircuitBreaker,
    CircuitState,
)


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


@pytest.mark.asyncio
async def test_open_for_infinite_seconds_is_clamped_to_finite_cap() -> None:
    """Finding 1 (Task 7+8 review): open_for(inf) must NOT permanently wedge
    the breaker OPEN — _maybe_promote_to_half_open's `elapsed >= inf` guard
    can never fire, so with no clamp the breaker never self-heals. Assert
    retry_after_seconds is finite and bounded by the module's backoff cap.
    """
    clock = _FakeClock()
    breaker = CircuitBreaker(provider_name="p", clock=clock)

    await breaker.open_for(float("inf"))

    assert breaker.state is CircuitState.OPEN
    retry_after = breaker.retry_after_seconds
    assert math.isfinite(retry_after)
    assert retry_after <= _HALF_OPEN_BACKOFF_CAP_SECONDS

    # And it actually self-heals: advancing past the cap promotes HALF_OPEN.
    clock.advance(_HALF_OPEN_BACKOFF_CAP_SECONDS + 1.0)
    assert breaker.state is CircuitState.HALF_OPEN


@pytest.mark.asyncio
async def test_open_for_nan_seconds_is_clamped_to_finite_cap() -> None:
    """Finding 1: open_for(nan) is worse than inf — nan comparisons are
    always False (permanently stuck OPEN) AND retry_after_seconds
    misleadingly reports 0.0 (max(0.0, nan) == 0.0) pre-fix. Assert the
    clamped value is finite and NOT nan.
    """
    clock = _FakeClock()
    breaker = CircuitBreaker(provider_name="p", clock=clock)

    await breaker.open_for(float("nan"))

    assert breaker.state is CircuitState.OPEN
    retry_after = breaker.retry_after_seconds
    assert math.isfinite(retry_after)
    assert not math.isnan(retry_after)
    assert retry_after == pytest.approx(_HALF_OPEN_BACKOFF_CAP_SECONDS)

    clock.advance(_HALF_OPEN_BACKOFF_CAP_SECONDS + 1.0)
    assert breaker.state is CircuitState.HALF_OPEN
