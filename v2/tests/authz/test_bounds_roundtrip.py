"""E2-S2 — BoundsSpec survives a JSON round-trip (SQLite persistence prereq).

frozenset/tuple axes round-trip by VALUE (order-insensitive); None (unrestricted)
and frozenset() (deny-all) are opposite and both must survive distinctly. Assert on
the MODEL, never the JSON string (frozenset dump order is non-deterministic).
"""

from __future__ import annotations

from stackowl.authz import BoundsSpec
from stackowl.authz.bounds import NetworkRule, ResourceCaps


def test_roundtrip_tools_and_axes_by_value() -> None:
    b = BoundsSpec(
        tools=frozenset({"read_file", "web_fetch"}),
        fs_read_roots=("/a", "/b"),
        network=(NetworkRule(host="example.com", port=443),),
        caps=ResourceCaps(max_steps=5),
    )
    assert BoundsSpec.model_validate_json(b.model_dump_json()) == b


def test_roundtrip_none_tools_is_unrestricted() -> None:
    b = BoundsSpec(tools=None)
    out = BoundsSpec.model_validate_json(b.model_dump_json())
    assert out.tools is None


def test_roundtrip_empty_allowlist_is_deny_all_not_none() -> None:
    b = BoundsSpec(tools=frozenset())
    out = BoundsSpec.model_validate_json(b.model_dump_json())
    assert out.tools == frozenset()
    assert out.tools is not None  # deny-all, NOT unrestricted


def test_equality_is_order_insensitive() -> None:
    a = BoundsSpec(tools=frozenset({"x", "y"}))
    b = BoundsSpec(tools=frozenset({"y", "x"}))
    assert a == b
