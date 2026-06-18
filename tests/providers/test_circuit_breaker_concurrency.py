"""T1 (F116) — CircuitBreaker concurrency: lock + single half-open probe.

The breaker's mutators (``record``) run under an ``asyncio.Lock`` and HALF_OPEN
admits EXACTLY ONE probe at a time (``admit_probe``). A probe that raises or is
cancelled MUST still release the in-flight flag (via the SP-2 caller ``finally``),
so the breaker can never wedge OPEN forever after a failed probe.

Drives the REAL ``resilient_round`` helper (the SP-2 per-round site) so the
admit/record/release contract is tested on the wire, not via private calls.
"""

from __future__ import annotations

import asyncio

import pytest

from stackowl.exceptions import CircuitOpenError
from stackowl.providers._resilient_round import resilient_round
from stackowl.providers.circuit_breaker import CircuitBreaker, CircuitState

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


def _fault(_e: BaseException) -> bool:
    """Treat every exception as a provider fault for these unit tests."""
    return True


async def _open(breaker: CircuitBreaker) -> None:
    for _ in range(3):
        await breaker.record(ok=False)
    assert breaker.state is CircuitState.OPEN


async def test_half_open_admits_exactly_one_probe() -> None:
    """Two concurrent rounds through a HALF_OPEN breaker: ONE probes, ONE is rejected."""
    clock = _ManualClock(0.0)
    breaker = CircuitBreaker(
        provider_name="p", failure_threshold=3, half_open_seconds=30, clock=clock
    )
    await _open(breaker)
    clock.advance(31.0)  # OPEN -> HALF_OPEN window elapsed
    assert breaker.state is CircuitState.HALF_OPEN

    started = asyncio.Event()
    release = asyncio.Event()

    async def _slow_round() -> str:
        started.set()
        await release.wait()  # hold the probe in-flight
        return "ok"

    async def _admitted() -> str:
        return await resilient_round(breaker, None, _slow_round, is_provider_fault=_fault)

    task = asyncio.create_task(_admitted())
    await started.wait()  # the first round is now the in-flight probe

    # A second concurrent round must be rejected while the probe is in flight.
    with pytest.raises(CircuitOpenError):
        await resilient_round(breaker, None, _slow_round, is_provider_fault=_fault)

    release.set()
    assert await task == "ok"
    # The successful probe closed the breaker.
    assert breaker.state is CircuitState.CLOSED


async def test_probe_that_raises_releases_flag_no_permanent_wedge() -> None:
    """A probe that RAISES must release the in-flight flag so the next probe is admitted."""
    clock = _ManualClock(0.0)
    breaker = CircuitBreaker(
        provider_name="p", failure_threshold=3, half_open_seconds=30, clock=clock
    )
    await _open(breaker)
    clock.advance(31.0)
    assert breaker.state is CircuitState.HALF_OPEN

    async def _boom() -> str:
        raise RuntimeError("probe failed")

    # The probe is admitted, fails, records a fault (HALF_OPEN -> OPEN) and the
    # in-flight flag is released in the caller's finally — NOT wedged.
    with pytest.raises(RuntimeError):
        await resilient_round(breaker, None, _boom, is_provider_fault=_fault)
    assert breaker.state is CircuitState.OPEN
    assert breaker._probe_in_flight is False  # the wedge guard

    # Re-enter HALF_OPEN; a fresh probe must again be admitted (no permanent wedge).
    clock.advance(31.0)
    assert breaker.state is CircuitState.HALF_OPEN

    async def _ok() -> str:
        return "good"

    assert await resilient_round(breaker, None, _ok, is_provider_fault=_fault) == "good"
    assert breaker.state is CircuitState.CLOSED
