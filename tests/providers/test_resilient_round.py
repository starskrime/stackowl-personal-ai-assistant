"""T2/T4 — the shared write seam: resilient_round records CLASSIFIED faults only.

* A classified upstream fault (SDK APIError / 5xx / 429 / connection-timeout)
  recorded N times opens the breaker.
* A user-stop (TurnStopped), a budget-kill (BudgetBreach), a durable-replay park,
  a malformed-args ValueError, and an empty-choices ProviderError do NOT count —
  they propagate unrecorded (no false breaker signal).
* Ordering (F117): when the breaker is OPEN, CircuitOpenError is raised BEFORE the
  limiter is asked for a token (no token burned on a short-circuited call).
* A configured RPM throttles a second round (the limiter awaits).

Drives the REAL resilient_round + REAL CircuitBreaker/RateLimiter; the round is a
plain coroutine raising the exception under test (no network).
"""

from __future__ import annotations

import asyncio

import pytest

from stackowl.exceptions import (
    BudgetBreach,
    CircuitOpenError,
    DurableReplayUncertain,
    ProviderError,
    TurnStopped,
)
from stackowl.providers._resilient_round import is_provider_fault, resilient_round
from stackowl.providers.circuit_breaker import CircuitBreaker, CircuitState
from stackowl.providers.rate_limiter import RateLimiter

pytestmark = pytest.mark.asyncio


class _ManualClock:
    def __init__(self, t0: float = 0.0) -> None:
        self._t = t0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self._t

    def now(self) -> object:
        from datetime import UTC, datetime

        return datetime.now(UTC)

    async def async_sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self._t += seconds

    def advance(self, dt: float) -> None:
        self._t += dt


def _raiser(exc: BaseException):
    async def _round() -> str:
        raise exc

    return _round


# --------------------------------------------------------------------------- #
# Classification — only real upstream faults open the breaker.
# --------------------------------------------------------------------------- #


async def test_classified_fault_opens_breaker() -> None:
    """A 5xx-shaped fault recorded failure_threshold times opens the breaker."""
    breaker = CircuitBreaker(provider_name="p", failure_threshold=3)

    class _ServerError(Exception):
        status_code = 503

    for _ in range(3):
        with pytest.raises(_ServerError):
            await resilient_round(breaker, None, _raiser(_ServerError()))
    assert breaker.state is CircuitState.OPEN


@pytest.mark.parametrize(
    "exc",
    [
        TurnStopped("req-1"),
        BudgetBreach("turn_seconds", 10.0, 11.0),
        DurableReplayUncertain("task-1", 0, "shell"),
        ValueError("malformed tool args"),
        ProviderError("p", ValueError("empty choices")),
    ],
)
async def test_non_fault_signals_do_not_open_breaker(exc: BaseException) -> None:
    """User-stop / budget-kill / durable-park / parse-error / empty-choices never count."""
    breaker = CircuitBreaker(provider_name="p", failure_threshold=3)
    for _ in range(5):
        with pytest.raises(type(exc)):
            await resilient_round(breaker, None, _raiser(exc))
    assert breaker.state is CircuitState.CLOSED, (
        f"{type(exc).__name__} wrongly counted as a provider fault"
    )


async def test_cancelled_error_is_not_a_fault() -> None:
    breaker = CircuitBreaker(provider_name="p", failure_threshold=3)
    for _ in range(5):
        with pytest.raises(asyncio.CancelledError):
            await resilient_round(breaker, None, _raiser(asyncio.CancelledError()))
    assert breaker.state is CircuitState.CLOSED


async def test_is_provider_fault_wrapped_sdk_error() -> None:
    """A ProviderError wrapping a 500-status transport error IS a fault."""

    class _Boom(Exception):
        status_code = 500

    assert is_provider_fault(ProviderError("p", _Boom())) is True
    # ...but wrapping an empty-choices ValueError is NOT.
    assert is_provider_fault(ProviderError("p", ValueError("empty choices"))) is False


# --------------------------------------------------------------------------- #
# F117 ordering — OPEN gate runs BEFORE the limiter acquire.
# --------------------------------------------------------------------------- #


async def test_breaker_open_skips_limiter_acquire() -> None:
    """When OPEN, CircuitOpenError raises BEFORE any limiter token is burned."""
    clock = _ManualClock(0.0)
    breaker = CircuitBreaker(provider_name="p", failure_threshold=3, clock=clock)
    for _ in range(3):
        await breaker.record(ok=False)
    assert breaker.state is CircuitState.OPEN

    limiter = RateLimiter(provider_name="p", capacity=5, refill_rate=1.0, clock=clock)
    tokens_before = limiter._tokens

    called = False

    async def _round() -> str:
        nonlocal called
        called = True
        return "x"

    with pytest.raises(CircuitOpenError):
        await resilient_round(breaker, limiter, _round)

    assert called is False, "the round ran despite an OPEN breaker"
    assert limiter._tokens == tokens_before, "a token was burned on a short-circuited call"


async def test_configured_rpm_throttles_second_round() -> None:
    """A limiter with one token forces the second round to await (sleep requested)."""
    clock = _ManualClock(0.0)
    breaker = CircuitBreaker(provider_name="p", failure_threshold=3, clock=clock)
    # capacity 1 → first round takes the only token; second must wait for refill.
    limiter = RateLimiter(provider_name="p", capacity=1, refill_rate=1.0, clock=clock)

    async def _ok() -> str:
        return "ok"

    assert await resilient_round(breaker, limiter, _ok) == "ok"
    assert not clock.sleeps  # first round immediate

    assert await resilient_round(breaker, limiter, _ok) == "ok"
    assert clock.sleeps, "second round did not await for a token (rpm not enforced)"


async def test_noop_limiter_and_no_breaker_passthrough() -> None:
    """No breaker + no-op limiter → byte-identical pass-through."""

    async def _ok() -> str:
        return "ok"

    assert await resilient_round(None, None, _ok) == "ok"
    noop = RateLimiter(provider_name="p", capacity=None)
    assert await resilient_round(None, noop, _ok) == "ok"
