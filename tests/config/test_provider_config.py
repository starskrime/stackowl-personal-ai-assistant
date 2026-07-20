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
