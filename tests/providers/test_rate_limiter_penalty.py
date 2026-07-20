"""FX-01 — RateLimiter.penalize(): a 429 shrinks the effective refill rate for a
cooldown window without touching the circuit breaker's outage-detection state.
"""

from __future__ import annotations

import pytest

from stackowl.providers.rate_limiter import RateLimiter

pytestmark = pytest.mark.asyncio


class _ManualClock:
    def __init__(self, t0: float = 0.0) -> None:
        self._t = t0

    def monotonic(self) -> float:
        return self._t

    def now(self) -> object:
        from datetime import UTC, datetime

        return datetime.now(UTC)

    async def async_sleep(self, seconds: float) -> None:
        self._t += seconds

    def advance(self, dt: float) -> None:
        self._t += dt


async def test_penalize_shrinks_refill_rate_for_the_window() -> None:
    clock = _ManualClock(0.0)
    limiter = RateLimiter(provider_name="p", capacity=10, refill_rate=1.0, clock=clock)
    # Drain the bucket to make refill visible.
    await limiter.acquire(10)
    assert limiter._tokens == 0.0

    limiter.penalize(factor=0.5, duration_seconds=30.0)
    clock.advance(10.0)
    limiter._refill()
    # At half rate, 10s should add ~5 tokens, not ~10.
    assert limiter._tokens == pytest.approx(5.0)


async def test_penalty_expires_after_duration() -> None:
    # NOTE: the effective rate is evaluated once per _refill() call (at its END
    # time), not integrated across the elapsed span — so a call whose interval
    # straddles the penalty boundary is a known approximation. This test keeps
    # each _refill() call's interval entirely on one side of the boundary.
    clock = _ManualClock(0.0)
    limiter = RateLimiter(provider_name="p", capacity=100, refill_rate=1.0, clock=clock)
    await limiter.acquire(100)
    assert limiter._tokens == 0.0

    limiter.penalize(factor=0.5, duration_seconds=10.0)
    clock.advance(4.0)
    limiter._refill()
    assert limiter._tokens == pytest.approx(2.0), "half rate while inside the penalty window"

    # Jump well past the window before the next refill — avoids the
    # single-call boundary approximation described above.
    limiter._tokens = 0.0
    limiter._last_refill = clock.monotonic()
    clock.advance(50.0)
    limiter._refill()
    assert limiter._tokens == pytest.approx(50.0), "full rate once the penalty window has expired"


async def test_penalize_is_noop_on_uncapped_limiter() -> None:
    limiter = RateLimiter(provider_name="p", capacity=None)
    limiter.penalize()  # must not raise
    assert limiter._penalty_until == 0.0


async def test_penalize_floors_factor_to_avoid_zero_effective_rate() -> None:
    clock = _ManualClock(0.0)
    limiter = RateLimiter(provider_name="p", capacity=10, refill_rate=1.0, clock=clock)
    limiter.penalize(factor=0.0, duration_seconds=10.0)
    assert limiter._effective_refill_rate() > 0.0
