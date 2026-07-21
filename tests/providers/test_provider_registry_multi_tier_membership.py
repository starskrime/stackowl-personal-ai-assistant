"""Tests for ProviderRegistry treating tier membership as containment
(a provider in multiple tiers), not equality."""

from __future__ import annotations

from stackowl.config.provider import ProviderConfig
from stackowl.providers.mock_provider import MockProvider
from stackowl.providers.registry import ModelRoute, ProviderRegistry


def test_register_mock_still_accepts_a_single_tier_string() -> None:
    """Regression: the ~98 existing register_mock(..., tier="fast") call
    sites across the test suite must keep working unchanged."""
    registry = ProviderRegistry()
    registry.register_mock("a", MockProvider(name="a"), tier="fast")
    assert registry.get_with_cascade("fast").name == "a"


def test_a_provider_registered_via_from_settings_with_multiple_tiers_is_selectable_from_both() -> None:
    config = ProviderConfig(
        name="groq", protocol="openai", default_model="m",
        tiers=("fast", "standard"), base_url="http://localhost:1",
    )

    class _FakeSettings:
        providers = [config]

    registry = ProviderRegistry.from_settings(_FakeSettings())  # type: ignore[arg-type]

    assert registry.get_with_cascade("fast").name == "groq"
    assert registry.get_with_cascade("standard").name == "groq"


def test_get_by_tier_finds_a_provider_present_in_a_non_primary_tier() -> None:
    config = ProviderConfig(
        name="groq", protocol="openai", default_model="m",
        tiers=("fast", "powerful"), base_url="http://localhost:1",
    )

    class _FakeSettings:
        providers = [config]

    registry = ProviderRegistry.from_settings(_FakeSettings())  # type: ignore[arg-type]

    assert registry.get_by_tier("powerful").name == "groq"


def test_resolve_capable_or_degrade_treats_multi_tier_membership_as_independent_per_tier() -> None:
    config = ProviderConfig(
        name="groq", protocol="openai", default_model="m",
        tiers=("fast", "powerful"), base_url="http://localhost:1",
    )

    class _FakeSettings:
        providers = [config]

    registry = ProviderRegistry.from_settings(_FakeSettings())  # type: ignore[arg-type]

    provider, degraded_from = registry.resolve_capable_or_degrade("powerful")
    assert provider.name == "groq"
    assert degraded_from is None  # exact match, not a substitution


class TestModelRouteStorage:
    def test_register_mock_default_single_model_route(self) -> None:
        """Backward-compat: register_mock with no `models=` kwarg behaves
        exactly as today — one ModelRoute with model="" (provider's own
        default) and the given tier."""
        registry = ProviderRegistry()
        registry.register_mock("acme", MockProvider(name="acme"), tier="fast")
        routes = registry._tiers["acme"]  # noqa: SLF001 — internal-shape test
        assert routes == (ModelRoute(model="", tiers=("fast",)),)

    def test_register_mock_with_explicit_models(self) -> None:
        registry = ProviderRegistry()
        registry.register_mock(
            "acme", MockProvider(name="acme"),
            models=(
                ModelRoute(model="acme-v1", tiers=("fast",)),
                ModelRoute(model="acme-v1-mini", tiers=("standard",)),
            ),
        )
        routes = registry._tiers["acme"]  # noqa: SLF001
        assert routes == (
            ModelRoute(model="acme-v1", tiers=("fast",)),
            ModelRoute(model="acme-v1-mini", tiers=("standard",)),
        )

    def test_from_settings_builds_default_model_plus_models_list(self) -> None:
        from stackowl.config.provider import ModelOverride, ProviderConfig
        from stackowl.config.settings import Settings

        settings = Settings.model_construct(
            providers=[
                ProviderConfig(
                    name="acme", protocol="openai", default_model="acme-v1",
                    tiers=("fast",), api_key=None,
                    models=(ModelOverride(name="acme-v1-mini", tiers=("standard",)),),
                )
            ]
        )
        registry = ProviderRegistry.from_settings(settings)
        routes = registry._tiers["acme"]  # noqa: SLF001
        assert routes == (
            ModelRoute(model="acme-v1", tiers=("fast",)),
            ModelRoute(model="acme-v1-mini", tiers=("standard",)),
        )
