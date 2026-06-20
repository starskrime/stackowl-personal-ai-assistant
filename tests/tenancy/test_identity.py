"""Tests for IdentityResolver and load_identity_resolver() (Task 1)."""
from __future__ import annotations

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
