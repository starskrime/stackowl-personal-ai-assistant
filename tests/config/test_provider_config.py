"""Tests for ProviderConfig — new fields."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from stackowl.config.provider import ProviderConfig


def test_cooldown_hours_defaults_none() -> None:
    cfg = ProviderConfig(
        name="x", protocol="openai", default_model="m", tier="fast",
    )
    assert cfg.cooldown_hours is None


def test_cooldown_hours_accepts_float() -> None:
    cfg = ProviderConfig(
        name="x", protocol="openai", default_model="m", tier="fast",
        cooldown_hours=24.0,
    )
    assert cfg.cooldown_hours == 24.0


def test_tiers_accepts_a_tuple_directly() -> None:
    cfg = ProviderConfig(
        name="x", protocol="openai", default_model="m", tiers=("fast", "standard"),
    )
    assert cfg.tiers == ("fast", "standard")


def test_legacy_tier_kwarg_is_normalized_to_a_one_item_tiers_tuple() -> None:
    cfg = ProviderConfig(
        name="x", protocol="openai", default_model="m", tier="fast",
    )
    assert cfg.tiers == ("fast",)
    assert not hasattr(cfg, "tier")  # the field itself no longer exists


def test_tiers_rejects_empty() -> None:
    with pytest.raises(ValidationError):
        ProviderConfig(name="x", protocol="openai", default_model="m", tiers=())


def test_tiers_rejects_duplicates() -> None:
    with pytest.raises(ValidationError):
        ProviderConfig(name="x", protocol="openai", default_model="m", tiers=("fast", "fast"))


def test_tiers_rejects_unknown_value() -> None:
    with pytest.raises(ValidationError):
        ProviderConfig(name="x", protocol="openai", default_model="m", tiers=("ultra",))


def test_neither_tier_nor_tiers_given_is_required_error() -> None:
    with pytest.raises(ValidationError):
        ProviderConfig(name="x", protocol="openai", default_model="m")


def test_legacy_tier_kwarg_accepts_a_non_string_sequence() -> None:
    cfg = ProviderConfig(
        name="x", protocol="openai", default_model="m", tier=["fast", "standard"],
    )
    assert cfg.tiers == ("fast", "standard")


def test_tiers_wins_when_both_tier_and_tiers_are_passed() -> None:
    cfg = ProviderConfig(
        name="x", protocol="openai", default_model="m",
        tier="fast", tiers=("standard", "powerful"),
    )
    assert cfg.tiers == ("standard", "powerful")


def _base_kwargs(**overrides: object) -> dict:
    kwargs = {
        "name": "acme",
        "protocol": "openai",
        "default_model": "acme-v1",
        "tiers": ("fast",),
    }
    kwargs.update(overrides)
    return kwargs


class TestModelOverride:
    def test_accepts_minimal_shape(self) -> None:
        from stackowl.config.provider import ModelOverride
        m = ModelOverride(name="acme-v1-mini", tiers=("standard",))
        assert m.name == "acme-v1-mini"
        assert m.tiers == ("standard",)
        assert m.max_output_tokens is None
        assert m.context_chars is None

    def test_accepts_explicit_overrides(self) -> None:
        from stackowl.config.provider import ModelOverride
        m = ModelOverride(
            name="acme-v1-mini", tiers=("standard",),
            max_output_tokens=50000, context_chars=80000,
        )
        assert m.max_output_tokens == 50000
        assert m.context_chars == 80000

    def test_rejects_empty_tiers(self) -> None:
        from stackowl.config.provider import ModelOverride
        with pytest.raises(ValidationError):
            ModelOverride(name="acme-v1-mini", tiers=())

    def test_rejects_duplicate_tiers(self) -> None:
        from stackowl.config.provider import ModelOverride
        with pytest.raises(ValidationError):
            ModelOverride(name="acme-v1-mini", tiers=("fast", "fast"))


class TestProviderConfigModels:
    def test_models_defaults_to_empty(self) -> None:
        cfg = ProviderConfig(**_base_kwargs())
        assert cfg.models == ()

    def test_accepts_one_additional_model(self) -> None:
        from stackowl.config.provider import ModelOverride
        cfg = ProviderConfig(**_base_kwargs(
            models=(ModelOverride(name="acme-v1-mini", tiers=("standard",)),),
        ))
        assert len(cfg.models) == 1
        assert cfg.models[0].name == "acme-v1-mini"

    def test_rejects_model_name_colliding_with_default_model(self) -> None:
        from stackowl.config.provider import ModelOverride
        with pytest.raises(ValidationError):
            ProviderConfig(**_base_kwargs(
                default_model="acme-v1",
                models=(ModelOverride(name="acme-v1", tiers=("standard",)),),
            ))

    def test_rejects_duplicate_model_names(self) -> None:
        from stackowl.config.provider import ModelOverride
        with pytest.raises(ValidationError):
            ProviderConfig(**_base_kwargs(
                models=(
                    ModelOverride(name="acme-v1-mini", tiers=("standard",)),
                    ModelOverride(name="acme-v1-mini", tiers=("powerful",)),
                ),
            ))

    def test_existing_config_without_models_key_unaffected(self) -> None:
        # Simulates loading a legacy YAML dict with no "models" key at all.
        cfg = ProviderConfig.model_validate(_base_kwargs())
        assert cfg.models == ()
