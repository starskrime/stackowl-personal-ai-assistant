"""Tests for TierSelector — round-robin across healthy providers in a tier."""

from __future__ import annotations

from stackowl.infra.clock import WallClock
from stackowl.providers.circuit_breaker import CircuitBreaker, CircuitState
from stackowl.providers.tier_selector import TierSelector


def _providers(*names: str) -> dict[str, object]:
    return {n: object() for n in names}


def test_round_robins_across_healthy_providers_in_tier() -> None:
    selector = TierSelector()
    providers = _providers("a", "b", "c")
    tiers = {"a": ("fast",), "b": ("fast",), "c": ("fast",)}
    breakers: dict[str, CircuitBreaker] = {}

    picks = [selector.select("fast", providers, tiers, breakers) for _ in range(6)]
    assert picks == ["a", "b", "c", "a", "b", "c"]


def test_skips_open_breaker() -> None:
    selector = TierSelector()
    providers = _providers("a", "b")
    tiers = {"a": ("fast",), "b": ("fast",)}
    breaker_b = CircuitBreaker(provider_name="b", failure_threshold=1, clock=WallClock())
    breakers = {"b": breaker_b}
    # Force b OPEN.
    breaker_b._state = CircuitState.OPEN  # test-only direct state set
    breaker_b._opened_at = breaker_b._clock.monotonic()

    picks = [selector.select("fast", providers, tiers, breakers) for _ in range(3)]
    assert picks == ["a", "a", "a"]


def test_empty_tier_returns_none() -> None:
    selector = TierSelector()
    assert selector.select("powerful", {}, {}, {}) is None


def test_all_open_returns_none() -> None:
    selector = TierSelector()
    providers = _providers("a")
    tiers = {"a": ("fast",)}
    breaker_a = CircuitBreaker(provider_name="a", clock=WallClock())
    breaker_a._state = CircuitState.OPEN
    breaker_a._opened_at = breaker_a._clock.monotonic()
    breakers = {"a": breaker_a}

    assert selector.select("fast", providers, tiers, breakers) is None


def test_cursor_is_per_tier_independent() -> None:
    selector = TierSelector()
    providers = _providers("fast-a", "fast-b", "std-a")
    tiers = {"fast-a": ("fast",), "fast-b": ("fast",), "std-a": ("standard",)}
    breakers: dict = {}

    assert selector.select("fast", providers, tiers, breakers) == "fast-a"
    assert selector.select("standard", providers, tiers, breakers) == "std-a"
    assert selector.select("fast", providers, tiers, breakers) == "fast-b"


def test_a_provider_in_two_tiers_is_independently_selectable_from_both() -> None:
    """The core new capability: one provider present in BOTH tiers' pools,
    selectable from each tier's own round-robin independently."""
    selector = TierSelector()
    providers = _providers("multi", "fast-only")
    tiers = {"multi": ("fast", "standard"), "fast-only": ("fast",)}
    breakers: dict[str, CircuitBreaker] = {}

    assert selector.select("standard", providers, tiers, breakers) == "multi"
    # "fast" tier round-robins between "multi" and "fast-only" independently
    # of the "standard" pick above.
    fast_picks = {selector.select("fast", providers, tiers, breakers) for _ in range(2)}
    assert fast_picks == {"multi", "fast-only"}
