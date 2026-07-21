from __future__ import annotations

import pytest

from stackowl.exceptions import AllProvidersUnavailableError
from stackowl.providers.mock_provider import MockProvider
from stackowl.providers.registry import ProviderRegistry


class _ManualClock:
    """Minimal Clock implementation with manually-advanced monotonic time."""

    def __init__(self, t0: float = 0.0) -> None:
        self._t = t0

    def monotonic(self) -> float:
        return self._t

    def now(self) -> object:
        from datetime import UTC, datetime
        return datetime.now(UTC)

    def advance(self, dt: float) -> None:
        self._t += dt


def _open_breaker(registry: ProviderRegistry, name: str) -> None:
    """Trip a provider's breaker to OPEN (threshold is 3 failures)."""
    breaker = registry._breakers[name]
    for _ in range(3):
        breaker._record_failure()
    from stackowl.providers.circuit_breaker import CircuitState
    assert breaker.state is CircuitState.OPEN


def test_healthy_primary_matches_get_by_tier():
    reg = ProviderRegistry()
    reg.register_mock("powerful_a", MockProvider(name="powerful_a"), tier="powerful")
    reg.register_mock("fast_b", MockProvider(name="fast_b"), tier="fast")
    provider, _model, degraded_from = reg.resolve_tier_with_fallback("powerful")
    assert provider is reg.get_by_tier("powerful")[0]
    assert degraded_from is None


def test_open_primary_falls_back_to_healthy_and_reports_name():
    reg = ProviderRegistry()
    reg.register_mock("powerful_a", MockProvider(name="powerful_a"), tier="powerful")
    reg.register_mock("fast_b", MockProvider(name="fast_b"), tier="fast")
    _open_breaker(reg, "powerful_a")
    provider, _model, degraded_from = reg.resolve_tier_with_fallback("powerful")
    assert provider.name == "fast_b"
    assert degraded_from == "powerful_a"


def test_all_open_raises():
    reg = ProviderRegistry()
    reg.register_mock("powerful_a", MockProvider(name="powerful_a"), tier="powerful")
    reg.register_mock("fast_b", MockProvider(name="fast_b"), tier="fast")
    _open_breaker(reg, "powerful_a")
    _open_breaker(reg, "fast_b")
    with pytest.raises(AllProvidersUnavailableError):
        reg.resolve_tier_with_fallback("powerful")


def test_no_tier_match_degrades_like_get_by_tier():
    reg = ProviderRegistry()
    reg.register_mock("only_fast", MockProvider(name="only_fast"), tier="fast")
    provider, _model, degraded_from = reg.resolve_tier_with_fallback("powerful")
    assert provider.name == "only_fast"
    assert degraded_from is None


def test_half_open_primary_is_used_not_fallen_back():
    """A HALF_OPEN breaker is treated as usable — no fallback triggered."""
    from stackowl.providers.circuit_breaker import CircuitState

    # half_open_seconds default is 30 (read from CircuitBreaker.__init__)
    clock = _ManualClock(t0=0.0)
    reg = ProviderRegistry(clock=clock)
    reg.register_mock("powerful_a", MockProvider(name="powerful_a"), tier="powerful")
    reg.register_mock("fast_b", MockProvider(name="fast_b"), tier="fast")

    breaker = reg._breakers["powerful_a"]
    for _ in range(3):
        breaker._record_failure()
    assert breaker.state is CircuitState.OPEN

    # Advance past the half_open_seconds window (default=30) so the state
    # property promotes OPEN → HALF_OPEN on the next read.
    clock.advance(31.0)
    assert breaker.state is CircuitState.HALF_OPEN  # promoted on read — not still OPEN

    provider, _model, degraded_from = reg.resolve_tier_with_fallback("powerful")
    assert provider.name == "powerful_a"  # HALF_OPEN is usable — primary returned
    assert degraded_from is None
