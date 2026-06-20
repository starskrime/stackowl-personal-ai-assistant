"""Tests for IdentityResolver and load_identity_resolver() (Task 1)."""
from __future__ import annotations

import pytest

from stackowl.tenancy.identity import IdentityResolver, load_identity_resolver


def test_mapped_handle_resolves_to_identity() -> None:
    r = IdentityResolver({"owner-primary": ["telegram:123", "slack:U0ABC", "local"]})
    assert r.resolve("telegram:123") == "owner-primary"
    assert r.resolve("slack:U0ABC") == "owner-primary"


def test_unmapped_handle_returns_itself() -> None:
    r = IdentityResolver({"owner-primary": ["telegram:123"]})
    assert r.resolve("telegram:999") == "telegram:999"  # unconfigured = identity behavior


def test_empty_map_is_identity() -> None:
    assert IdentityResolver({}).resolve("slack:x") == "slack:x"


def test_malformed_alias_value_degrades_not_crashes() -> None:
    # A non-list alias value must not crash resolution.
    r = IdentityResolver({"bad": "telegram:123"})  # type: ignore[dict-item]
    assert r.resolve("telegram:123") == "telegram:123"


def test_load_identity_resolver_unconfigured_is_identity() -> None:
    # When no identity section is configured, resolve(x) == x (byte-identical behavior).
    resolver = load_identity_resolver()
    assert resolver.resolve("telegram:999") == "telegram:999"
    assert resolver.resolve("slack:U0ABC") == "slack:U0ABC"
    assert resolver.resolve("local") == "local"


def test_load_identity_resolver_degrades_when_settings_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """load_identity_resolver() must not raise when Settings() construction fails.

    It should return an identity-behaving IdentityResolver({}) so callers are
    never broken by a config failure (no-hidden-errors invariant).
    """
    def _boom() -> None:
        raise RuntimeError("settings DB corrupted")

    monkeypatch.setattr("stackowl.config.settings.Settings", _boom)

    # Must not raise
    resolver = load_identity_resolver()

    # Identity behaviour: unmapped handle returns itself
    assert resolver.resolve("telegram:1") == "telegram:1"
    assert resolver.resolve("slack:XYZ") == "slack:XYZ"
