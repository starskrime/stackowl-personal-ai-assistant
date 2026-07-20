"""Tests for ProviderRegistry multi-provider-per-tier round-robin (via TierSelector)."""

from __future__ import annotations

from stackowl.providers.mock_provider import MockProvider
from stackowl.providers.registry import ProviderRegistry


def _registry_with(*names_and_tiers: tuple[str, str]) -> ProviderRegistry:
    registry = ProviderRegistry()
    for name, tier in names_and_tiers:
        registry.register_mock(name, MockProvider(name=name), tier=tier)
    return registry


def test_multiple_providers_same_tier_round_robin() -> None:
    registry = _registry_with(("a", "fast"), ("b", "fast"))
    picks = [registry.get_with_cascade("fast").name for _ in range(4)]
    assert picks == ["a", "b", "a", "b"]


def test_single_provider_per_tier_unchanged() -> None:
    """Regression: existing single-provider-per-tier behavior stays identical."""
    registry = _registry_with(("only", "fast"))
    for _ in range(3):
        assert registry.get_with_cascade("fast").name == "only"
