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
    assert registry.get_with_cascade("fast")[0].name == "a"


def test_a_provider_registered_via_from_settings_with_multiple_tiers_is_selectable_from_both() -> None:
    config = ProviderConfig(
        name="groq", protocol="openai", default_model="m",
        tiers=("fast", "standard"), base_url="http://localhost:1",
    )

    class _FakeSettings:
        providers = [config]

    registry = ProviderRegistry.from_settings(_FakeSettings())  # type: ignore[arg-type]

    assert registry.get_with_cascade("fast")[0].name == "groq"
    assert registry.get_with_cascade("standard")[0].name == "groq"


def test_get_by_tier_finds_a_provider_present_in_a_non_primary_tier() -> None:
    config = ProviderConfig(
        name="groq", protocol="openai", default_model="m",
        tiers=("fast", "powerful"), base_url="http://localhost:1",
    )

    class _FakeSettings:
        providers = [config]

    registry = ProviderRegistry.from_settings(_FakeSettings())  # type: ignore[arg-type]

    assert registry.get_by_tier("powerful")[0].name == "groq"


def test_resolve_capable_or_degrade_treats_multi_tier_membership_as_independent_per_tier() -> None:
    config = ProviderConfig(
        name="groq", protocol="openai", default_model="m",
        tiers=("fast", "powerful"), base_url="http://localhost:1",
    )

    class _FakeSettings:
        providers = [config]

    registry = ProviderRegistry.from_settings(_FakeSettings())  # type: ignore[arg-type]

    provider, _model, degraded_from = registry.resolve_capable_or_degrade("powerful")
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


def test_tiers_of_flattens_across_model_routes() -> None:
    """A provider with 2 models in DIFFERENT tiers must report BOTH tiers via
    tiers_of — the vision selector's contract is provider-level membership,
    regardless of which model serves which tier."""
    registry = ProviderRegistry()
    mock = MockProvider(name="acme")
    registry.register_mock(
        "acme", mock,
        models=(
            ModelRoute(model="acme-v1", tiers=("fast",)),
            ModelRoute(model="acme-v1-mini", tiers=("standard",)),
        ),
    )
    assert registry.tiers_of(mock) == ("fast", "standard")


def test_tiers_of_dedupes_a_tier_served_by_two_models() -> None:
    registry = ProviderRegistry()
    mock = MockProvider(name="acme")
    registry.register_mock(
        "acme", mock,
        models=(
            ModelRoute(model="acme-v1", tiers=("fast",)),
            ModelRoute(model="acme-v1-mini", tiers=("fast", "standard")),
        ),
    )
    assert registry.tiers_of(mock) == ("fast", "standard")


class TestAndModelResolution:
    def test_get_by_tier_returns_default_model_route(self) -> None:
        registry = ProviderRegistry()
        registry.register_mock(
            "acme", MockProvider(name="acme"),
            models=(ModelRoute(model="acme-v1", tiers=("fast",)),),
        )
        provider, model = registry.get_by_tier("fast")
        assert provider.name == "acme"
        assert model == "acme-v1"

    def test_get_by_tier_picks_correct_model_among_several(self) -> None:
        registry = ProviderRegistry()
        registry.register_mock(
            "acme", MockProvider(name="acme"),
            models=(
                ModelRoute(model="acme-v1", tiers=("fast",)),
                ModelRoute(model="acme-v1-mini", tiers=("standard",)),
            ),
        )
        provider, model = registry.get_by_tier("standard")
        assert provider.name == "acme"
        assert model == "acme-v1-mini"

    def test_get_with_cascade_returns_model(self) -> None:
        registry = ProviderRegistry()
        registry.register_mock(
            "acme", MockProvider(name="acme"),
            models=(ModelRoute(model="acme-v1", tiers=("fast",)),),
        )
        provider, model = registry.get_with_cascade("fast")
        assert provider.name == "acme"
        assert model == "acme-v1"

    def test_resolve_tier_with_fallback_returns_three_tuple(self) -> None:
        registry = ProviderRegistry()
        registry.register_mock(
            "acme", MockProvider(name="acme"),
            models=(ModelRoute(model="acme-v1", tiers=("fast",)),),
        )
        provider, model, degraded = registry.resolve_tier_with_fallback("fast")
        assert provider.name == "acme"
        assert model == "acme-v1"
        assert degraded is None

    def test_resolve_capable_or_degrade_returns_three_tuple(self) -> None:
        registry = ProviderRegistry()
        registry.register_mock(
            "acme", MockProvider(name="acme"),
            models=(ModelRoute(model="acme-v1", tiers=("powerful",)),),
        )
        provider, model, degraded = registry.resolve_capable_or_degrade("powerful")
        assert provider.name == "acme"
        assert model == "acme-v1"
        assert degraded is None


class TestSameTierMultiModelRoundRobin:
    def test_round_robins_between_two_models_of_one_provider_in_the_same_tier(self) -> None:
        registry = ProviderRegistry()
        registry.register_mock(
            "acme", MockProvider(name="acme"),
            models=(
                ModelRoute(model="acme-v1", tiers=("fast",)),
                ModelRoute(model="acme-v1-fast2", tiers=("fast",)),
            ),
        )
        first = registry.get_by_tier("fast")[1]
        second = registry.get_by_tier("fast")[1]
        third = registry.get_by_tier("fast")[1]
        assert {first, second} == {"acme-v1", "acme-v1-fast2"}
        assert first != second
        assert third == first  # cursor wraps after 2

    def test_single_matching_route_is_unaffected_no_cursor_bookkeeping(self) -> None:
        registry = ProviderRegistry()
        registry.register_mock(
            "acme", MockProvider(name="acme"),
            models=(ModelRoute(model="acme-v1", tiers=("fast",)),),
        )
        assert registry.get_by_tier("fast")[1] == "acme-v1"
        assert registry.get_by_tier("fast")[1] == "acme-v1"
