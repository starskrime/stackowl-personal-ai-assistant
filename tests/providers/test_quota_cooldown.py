"""Tests for quota-aware cooldown: reset-header parsing, cooldown_hours fallback."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from stackowl.config.provider import ProviderConfig
from stackowl.infra.clock import Clock
from stackowl.providers._resilient_round import resilient_round
from stackowl.providers.circuit_breaker import CircuitBreaker, CircuitState
from stackowl.providers.rate_limiter import RateLimiter
from stackowl.providers.registry import ProviderRegistry


class _FakeClock(Clock):
    def __init__(self) -> None:
        self._t = 0.0

    def monotonic(self) -> float:
        return self._t

    async def async_sleep(self, seconds: float) -> None:
        self._t += seconds


class _RateLimited429(Exception):
    def __init__(self, retry_after: str | None) -> None:
        super().__init__("429 rate limited")
        self.status_code = 429
        self.response = SimpleNamespace(headers={"retry-after": retry_after} if retry_after else {})


@pytest.mark.asyncio
async def test_parseable_reset_header_opens_breaker_for_that_exact_duration() -> None:
    clock = _FakeClock()
    breaker = CircuitBreaker(provider_name="p", clock=clock)
    limiter = RateLimiter.from_rpm("p", None, clock=clock)

    async def failing_round() -> None:
        raise _RateLimited429(retry_after="120")

    with pytest.raises(_RateLimited429):
        await resilient_round(breaker, limiter, failing_round, cooldown_hours=None)

    assert breaker.state is CircuitState.OPEN
    assert breaker.retry_after_seconds == pytest.approx(120.0)


@pytest.mark.asyncio
async def test_no_reset_header_falls_back_to_configured_cooldown_hours() -> None:
    clock = _FakeClock()
    breaker = CircuitBreaker(provider_name="p", clock=clock)
    limiter = RateLimiter.from_rpm("p", None, clock=clock)

    async def failing_round() -> None:
        raise _RateLimited429(retry_after=None)

    with pytest.raises(_RateLimited429):
        await resilient_round(breaker, limiter, failing_round, cooldown_hours=1.0)

    assert breaker.state is CircuitState.OPEN
    assert breaker.retry_after_seconds == pytest.approx(3600.0)


@pytest.mark.asyncio
async def test_no_header_and_no_cooldown_hours_uses_generic_threshold_path() -> None:
    """Absent both signals: byte-identical to today (penalize only, no open_for)."""
    clock = _FakeClock()
    breaker = CircuitBreaker(provider_name="p", failure_threshold=3, clock=clock)
    limiter = RateLimiter.from_rpm("p", None, clock=clock)

    async def failing_round() -> None:
        raise _RateLimited429(retry_after=None)

    with pytest.raises(_RateLimited429):
        await resilient_round(breaker, limiter, failing_round, cooldown_hours=None)

    # One failure recorded via the generic path — NOT forced OPEN (threshold is 3).
    assert breaker.state is CircuitState.CLOSED


@pytest.mark.asyncio
async def test_malformed_reset_header_falls_back_to_cooldown_hours_not_crash() -> None:
    clock = _FakeClock()
    breaker = CircuitBreaker(provider_name="p", clock=clock)
    limiter = RateLimiter.from_rpm("p", None, clock=clock)

    async def failing_round() -> None:
        raise _RateLimited429(retry_after="not-a-number")

    with pytest.raises(_RateLimited429):
        await resilient_round(breaker, limiter, failing_round, cooldown_hours=2.0)

    assert breaker.state is CircuitState.OPEN
    assert breaker.retry_after_seconds == pytest.approx(7200.0)


def _cfg(**overrides: object) -> ProviderConfig:
    base: dict[str, object] = dict(
        name="p", protocol="openai", default_model="m", tier="fast",
        api_key=None, base_url=None,
    )
    base.update(overrides)
    return ProviderConfig(**base)


def test_apply_settings_updates_cooldown_hours_on_unchanged_provider() -> None:
    """A config-only cooldown_hours change is picked up on reload, mirroring
    how other config field changes already flow through apply_settings."""
    registry = ProviderRegistry.from_settings(SimpleNamespace(providers=[_cfg(cooldown_hours=None)]))
    assert registry.get("p")._cooldown_hours is None

    registry.apply_settings(SimpleNamespace(providers=[_cfg(cooldown_hours=6.0)]))
    assert registry.get("p")._cooldown_hours == 6.0
