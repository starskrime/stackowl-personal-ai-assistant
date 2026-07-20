"""Tests for ProviderConfig — new fields."""

from __future__ import annotations

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
