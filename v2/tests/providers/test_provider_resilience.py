"""Self-healing tests for ModelProvider + ProviderRegistry CircuitBreaker."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Literal

import pytest

from stackowl.exceptions import CircuitOpenError
from stackowl.infra.clock import Clock, WallClock
from stackowl.infra.resilience import HealableResource
from stackowl.providers.base import CompletionResult, Message, ModelProvider
from stackowl.providers.circuit_breaker import CircuitBreaker, CircuitState
from stackowl.providers.mock_provider import MockProvider


class _ManualClock:
    """Minimal Clock implementation that returns a manually-advanced monotonic time."""

    def __init__(self, t0: float = 0.0) -> None:
        self._t = t0

    def monotonic(self) -> float:
        return self._t

    def now(self) -> object:
        from datetime import UTC, datetime
        return datetime.now(UTC)

    def advance(self, dt: float) -> None:
        self._t += dt

pytestmark = pytest.mark.asyncio


class _FailingProvider(ModelProvider):
    """Provider whose complete() always raises."""

    def __init__(self, name: str = "failing") -> None:
        self._name = name
        self.call_count = 0

    @property
    def name(self) -> str:
        return self._name

    @property
    def protocol(self) -> Literal["openai", "anthropic", "gemini"]:
        return "openai"

    async def complete(self, messages: list[Message], model: str, **kwargs: object) -> CompletionResult:
        self.call_count += 1
        raise RuntimeError("simulated upstream failure")

    async def stream(self, messages: list[Message], model: str, **kwargs: object) -> AsyncIterator[str]:
        self.call_count += 1
        raise RuntimeError("simulated upstream failure")
        yield ""  # unreachable — for typing


async def test_provider_satisfies_healable_resource_protocol() -> None:
    p = MockProvider(name="m")
    assert isinstance(p, HealableResource)


async def test_provider_available_by_default() -> None:
    p = MockProvider(name="m")
    assert p.available is True
    assert p.unavailable_reason is None


async def test_provider_ensure_available_is_noop() -> None:
    p = MockProvider(name="m")
    await p.ensure_available()
    assert p.available is True


async def test_provider_register_on_recycled_is_noop() -> None:
    p = MockProvider(name="m")
    fired: list[int] = []
    p.register_on_recycled(lambda: fired.append(1))
    # No way to trigger; just ensure call doesn't raise
    await p.ensure_available()
    assert fired == []


async def test_circuit_breaker_opens_after_threshold_failures() -> None:
    clock = WallClock()
    breaker = CircuitBreaker(
        provider_name="p1", failure_threshold=3,
        window_seconds=60, half_open_seconds=30, clock=clock,
    )
    provider = _FailingProvider()

    async def _call() -> CompletionResult:
        return await provider.complete([Message(role="user", content="x")], model="")

    for _ in range(3):
        with pytest.raises(RuntimeError):
            await breaker.call(_call())
    assert breaker.state is CircuitState.OPEN

    with pytest.raises(CircuitOpenError):
        await breaker.call(_call())
    # The 4th call should NOT hit the provider (circuit short-circuits)
    assert provider.call_count == 3


async def test_circuit_breaker_auto_recovers_open_to_half_open() -> None:
    clock = _ManualClock(0.0)
    breaker = CircuitBreaker(
        provider_name="p1", failure_threshold=2,
        window_seconds=60, half_open_seconds=30, clock=clock,
    )

    async def _fail() -> str:
        raise RuntimeError("boom")

    # Trip the breaker.
    for _ in range(2):
        with pytest.raises(RuntimeError):
            await breaker.call(_fail())
    assert breaker.state is CircuitState.OPEN

    # Advance time past half_open_seconds.
    clock.advance(31.0)
    assert breaker.state is CircuitState.HALF_OPEN


async def test_circuit_breaker_half_open_to_closed_on_success() -> None:
    clock = _ManualClock(0.0)
    breaker = CircuitBreaker(
        provider_name="p1", failure_threshold=2,
        window_seconds=60, half_open_seconds=30, clock=clock,
    )

    async def _fail() -> str:
        raise RuntimeError("boom")

    async def _ok() -> str:
        return "good"

    for _ in range(2):
        with pytest.raises(RuntimeError):
            await breaker.call(_fail())
    clock.advance(31.0)
    assert breaker.state is CircuitState.HALF_OPEN

    result = await breaker.call(_ok())
    assert result == "good"
    assert breaker.state is CircuitState.CLOSED
