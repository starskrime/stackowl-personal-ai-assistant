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
from stackowl.infra import retry_ledger
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
    ledger_token = retry_ledger.bind()
    try:
        with pytest.raises(CircuitOpenError):
            await resilient_round(breaker, None, _slow_round, is_provider_fault=_fault)
        # Workstream B — the ledger records the skip for cross-layer observability.
        events = retry_ledger.get_retry()
        assert [e.kind for e in events] == ["circuit_open_skip"]
        assert events[0].detail == "half_open_probe_in_flight"
    finally:
        retry_ledger.reset(ledger_token)

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

    # FX-02: a failed probe doubles the next cooldown (30s -> 60s), so the old
    # +31s isn't enough to re-enter HALF_OPEN this time.
    clock.advance(31.0)
    assert breaker.state is CircuitState.OPEN
    # Re-enter HALF_OPEN once the doubled window elapses; a fresh probe must
    # again be admitted (no permanent wedge).
    clock.advance(30.0)
    assert breaker.state is CircuitState.HALF_OPEN

    async def _ok() -> str:
        return "good"

    assert await resilient_round(breaker, None, _ok, is_provider_fault=_fault) == "good"
    assert breaker.state is CircuitState.CLOSED


async def test_half_open_backoff_doubles_on_repeated_probe_failures_and_caps() -> None:
    """FX-02: each failed HALF_OPEN probe doubles the next cooldown, capped."""
    clock = _ManualClock(0.0)
    breaker = CircuitBreaker(
        provider_name="p", failure_threshold=3, half_open_seconds=30, clock=clock
    )
    await _open(breaker)

    # 1st probe fails: cooldown 30 -> 60.
    clock.advance(31.0)
    assert breaker.state is CircuitState.HALF_OPEN
    await breaker.record(ok=False)
    assert breaker.state is CircuitState.OPEN
    assert breaker._current_half_open_seconds == 60.0

    # 2nd probe fails: cooldown 60 -> 120.
    clock.advance(61.0)
    assert breaker.state is CircuitState.HALF_OPEN
    await breaker.record(ok=False)
    assert breaker._current_half_open_seconds == 120.0

    # Repeated failures keep doubling but never exceed the cap.
    for _ in range(10):
        clock.advance(breaker._current_half_open_seconds + 1.0)
        assert breaker.state is CircuitState.HALF_OPEN
        await breaker.record(ok=False)
    assert breaker._current_half_open_seconds == 900.0


async def test_half_open_backoff_resets_to_base_after_success() -> None:
    """FX-02: a successful probe resets the window so the NEXT incident starts fresh."""
    clock = _ManualClock(0.0)
    breaker = CircuitBreaker(
        provider_name="p", failure_threshold=3, half_open_seconds=30, clock=clock
    )
    await _open(breaker)

    clock.advance(31.0)
    await breaker.record(ok=False)  # cooldown now 60s
    assert breaker._current_half_open_seconds == 60.0

    clock.advance(61.0)
    await breaker.record(ok=True)  # probe succeeds -> CLOSED, window resets
    assert breaker.state is CircuitState.CLOSED
    assert breaker._current_half_open_seconds == 30.0

    # A fresh, unrelated outage opens the breaker again and starts at base (30s),
    # not the previous incident's escalated 60s.
    await _open(breaker)
    clock.advance(31.0)
    assert breaker.state is CircuitState.HALF_OPEN
