"""Tests for ProviderRegistry multi-provider-per-tier round-robin (via TierSelector)."""

from __future__ import annotations

import pytest

from stackowl.exceptions import AllProvidersUnavailableError
from stackowl.providers.mock_provider import MockProvider
from stackowl.providers.registry import ProviderRegistry


def _registry_with(*names_and_tiers: tuple[str, str]) -> ProviderRegistry:
    registry = ProviderRegistry()
    for name, tier in names_and_tiers:
        registry.register_mock(name, MockProvider(name=name), tier=tier)
    return registry


class _FakeSelector:
    """Test double standing in for TierSelector — returns a scripted name sequence
    PER TIER, mirroring the real TierSelector's per-tier round-robin cursor.

    Lets a test deterministically control exactly which name ``get_with_cascade``
    sees on each call for a given tier, instead of racing real threads against
    the real round-robin cursor to reproduce a concurrent-removal window. Being
    tier-scoped (not one flat global sequence) matters: it stops a retry that
    accidentally spills into the NEXT tier's select() call from being mistaken
    for a same-tier retry succeeding — a tier with no scripted sequence (or an
    exhausted one) always returns ``None``, exactly like the real selector when
    a tier has no healthy candidates.
    """

    def __init__(self, sequence_by_tier: dict[str, list[str]]) -> None:
        self._iters = {tier: iter(seq) for tier, seq in sequence_by_tier.items()}

    def select(self, tier: str, providers: object, tiers: object, breakers: object) -> str | None:
        it = self._iters.get(tier)
        if it is None:
            return None
        return next(it, None)


def test_multiple_providers_same_tier_round_robin() -> None:
    registry = _registry_with(("a", "fast"), ("b", "fast"))
    picks = [registry.get_with_cascade("fast")[0].name for _ in range(4)]
    assert picks == ["a", "b", "a", "b"]


def test_single_provider_per_tier_unchanged() -> None:
    """Regression: existing single-provider-per-tier behavior stays identical."""
    registry = _registry_with(("only", "fast"))
    for _ in range(3):
        assert registry.get_with_cascade("fast")[0].name == "only"


def test_concurrent_removal_retries_within_tier_not_next_tier() -> None:
    """A selected name missing from the snapshot must retry WITHIN the same tier.

    Simulates the hot-reload race: TierSelector picks "a", but "a" was
    concurrently removed from the registry before get_with_cascade could look
    it up. The fake selector only has a scripted sequence for "fast" — every
    other tier returns None (no healthy candidate) — so this can ONLY resolve
    to "b" if the retry happens WITHIN the "fast" tier's own loop. Under the
    old behavior (fall through to the next tier on a miss), the first select()
    call would consume the "fast" attempt, the miss would fall through, and
    every subsequent tier would return None too — raising
    AllProvidersUnavailableError instead of returning "b".
    """
    registry = _registry_with(("a", "fast"), ("b", "fast"))
    registry._tier_selector = _FakeSelector({"fast": ["a", "b"]})  # type: ignore[assignment]
    del registry._providers["a"]  # concurrent removal, before get_with_cascade runs

    result = registry.get_with_cascade("fast")

    assert result[0].name == "b"


def test_concurrent_removal_retry_is_bounded_not_infinite() -> None:
    """When every name assigned to the tier is missing, the retry must give up.

    Bounded by the tier's assigned-name count so a fully-stale snapshot can
    never spin — proven here by handing the fake selector far more scripted
    names for "fast" than the bound should ever consume before it stops and
    the cascade raises (an unbounded retry would exhaust the 10-item sequence
    and crash on ``next()`` with no default, rather than raising the expected
    ``AllProvidersUnavailableError``).
    """
    registry = _registry_with(("a", "fast"), ("b", "fast"))
    registry._tier_selector = _FakeSelector({"fast": ["a", "b"] * 5})  # type: ignore[assignment]
    del registry._providers["a"]
    del registry._providers["b"]  # every name assigned to "fast" now missing from the snapshot

    with pytest.raises(AllProvidersUnavailableError):
        registry.get_with_cascade("fast")
