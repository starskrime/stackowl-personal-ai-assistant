"""Tests for quota-aware cooldown: reset-header parsing, cooldown_hours fallback."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from stackowl.config.provider import ProviderConfig
from stackowl.infra import retry_ledger
from stackowl.infra.clock import Clock
from stackowl.providers._resilient_round import _parse_retry_after_seconds, resilient_round
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

    ledger_token = retry_ledger.bind()
    try:
        with pytest.raises(_RateLimited429):
            await resilient_round(breaker, limiter, failing_round, cooldown_hours=None)

        assert breaker.state is CircuitState.OPEN
        assert breaker.retry_after_seconds == pytest.approx(120.0)
        # Workstream B — the ledger records the cooldown for cross-layer observability.
        events = retry_ledger.get_retry()
        cooldown_events = [e for e in events if e.kind == "cooldown"]
        assert len(cooldown_events) == 1
        assert cooldown_events[0].detail == "120s"
    finally:
        retry_ledger.reset(ledger_token)


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


# --------------------------------------------------------------------------- #
# Finding 1 (Task 7+8 review) — non-finite Retry-After values must never reach
# CircuitBreaker.open_for, since inf/nan there permanently wedges the breaker
# OPEN with no self-healing path (elapsed >= inf is never True; nan compares
# always False). float() happily parses "inf"/"Infinity"/"nan" strings, so
# _parse_retry_after_seconds must reject them explicitly.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("raw", ["inf", "Infinity", "-inf", "nan"])
def test_parse_retry_after_seconds_rejects_non_finite_values(raw: str) -> None:
    exc = _RateLimited429(retry_after=raw)
    assert _parse_retry_after_seconds(exc) is None


@pytest.mark.asyncio
async def test_infinite_reset_header_falls_back_to_cooldown_hours_not_stuck_open() -> None:
    """A malicious/malformed 'Retry-After: inf' header must NOT wedge the
    breaker open forever — it should be rejected by the parser and fall
    through to the configured cooldown_hours, same as any other unparseable
    header (test_malformed_reset_header_falls_back_to_cooldown_hours_not_crash)."""
    clock = _FakeClock()
    breaker = CircuitBreaker(provider_name="p", clock=clock)
    limiter = RateLimiter.from_rpm("p", None, clock=clock)

    async def failing_round() -> None:
        raise _RateLimited429(retry_after="inf")

    with pytest.raises(_RateLimited429):
        await resilient_round(breaker, limiter, failing_round, cooldown_hours=2.0)

    assert breaker.state is CircuitState.OPEN
    assert breaker.retry_after_seconds == pytest.approx(7200.0)


@pytest.mark.asyncio
async def test_nan_reset_header_falls_back_to_cooldown_hours_not_stuck_open() -> None:
    """Same as above for 'Retry-After: nan' — nan is worse than inf pre-fix
    because max(0.0, nan) == 0.0, so retry_after_seconds misleadingly
    reports 0.0 while the breaker is actually stuck OPEN forever."""
    clock = _FakeClock()
    breaker = CircuitBreaker(provider_name="p", clock=clock)
    limiter = RateLimiter.from_rpm("p", None, clock=clock)

    async def failing_round() -> None:
        raise _RateLimited429(retry_after="nan")

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
