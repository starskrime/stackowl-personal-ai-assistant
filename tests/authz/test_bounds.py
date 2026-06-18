"""E2-S1 — BoundsSpec model: closed enumeration, permits_tool, validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from stackowl.authz import BoundsSpec, BoundsViolation, NetworkRule, ResourceCaps


def test_permits_tool_none_axis_permits_everything() -> None:
    """tools=None (default) is unrestricted — every tool is permitted."""
    bounds = BoundsSpec()
    assert bounds.tools is None
    assert bounds.permits_tool("anything") is True
    assert bounds.permits_tool("read_file") is True


def test_permits_tool_allowlist_only_listed() -> None:
    """A present allowlist permits only its members."""
    bounds = BoundsSpec(tools=frozenset({"allowed_tool", "other"}))
    assert bounds.permits_tool("allowed_tool") is True
    assert bounds.permits_tool("other") is True
    assert bounds.permits_tool("forbidden_tool") is False


def test_permits_tool_empty_allowlist_permits_nothing() -> None:
    """An empty (but present) allowlist denies every tool."""
    bounds = BoundsSpec(tools=frozenset())
    assert bounds.permits_tool("anything") is False


def test_network_rule_construct_and_wildcards() -> None:
    rule = NetworkRule(host="api.example.com", port=443, scheme="https")
    assert (rule.host, rule.port, rule.scheme) == ("api.example.com", 443, "https")
    wild = NetworkRule(host="example.com")
    assert wild.port is None and wild.scheme is None


def test_empty_network_tuple_is_zero_egress() -> None:
    """An empty network tuple () is the explicit deny-all posture (not None)."""
    bounds = BoundsSpec(network=())
    assert bounds.network == ()
    assert bounds.network is not None  # distinct from unrestricted (None)


def test_network_none_is_unrestricted() -> None:
    assert BoundsSpec().network is None


def test_resource_caps_construct_all_none_default() -> None:
    caps = ResourceCaps()
    assert caps.max_cost_usd is None
    assert caps.max_time_s is None
    assert caps.max_steps is None
    assert caps.max_concurrency is None
    filled = ResourceCaps(max_cost_usd=1.5, max_time_s=30.0, max_steps=10, max_concurrency=2)
    assert filled.max_cost_usd == 1.5 and filled.max_steps == 10


def test_all_axes_construct_together() -> None:
    bounds = BoundsSpec(
        tools=frozenset({"t"}),
        fs_read_roots=("/ws/read",),
        fs_write_roots=("/ws/write",),
        network=(NetworkRule(host="h", port=80, scheme="http"),),
        data_owner_id="owner-1",
        data_namespaces=("ns1", "ns2"),
        caps=ResourceCaps(max_steps=5),
    )
    assert bounds.fs_read_roots == ("/ws/read",)
    assert bounds.fs_write_roots == ("/ws/write",)
    assert bounds.data_owner_id == "owner-1"
    assert bounds.data_namespaces == ("ns1", "ns2")
    assert bounds.caps.max_steps == 5


def test_bounds_is_frozen() -> None:
    bounds = BoundsSpec(tools=frozenset({"a"}))
    with pytest.raises(ValidationError):
        bounds.tools = frozenset({"b"})  # type: ignore[misc]


def test_bounds_rejects_unknown_axis() -> None:
    """Closed enumeration — extra='forbid' rejects an undeclared axis."""
    with pytest.raises(ValidationError):
        BoundsSpec(unknown_axis=True)  # type: ignore[call-arg]


def test_round_trip_serialization() -> None:
    original = BoundsSpec(
        tools=frozenset({"a", "b"}),
        network=(NetworkRule(host="h"),),
        caps=ResourceCaps(max_cost_usd=2.0),
    )
    restored = BoundsSpec.model_validate(original.model_dump())
    assert restored.permits_tool("a") is True
    assert restored.permits_tool("z") is False
    assert restored.network == (NetworkRule(host="h"),)
    assert restored.caps.max_cost_usd == 2.0


def test_bounds_violation_carries_axis_and_value() -> None:
    exc = BoundsViolation(axis="tools", value="forbidden_tool")
    assert exc.axis == "tools"
    assert exc.value == "forbidden_tool"
    assert "forbidden_tool" in str(exc)
    assert "tools" in str(exc)


# --- E2-S2 composition contract: BoundsSpec.intersect (narrowing-only) -----------


def test_intersect_none_and_none_is_unrestricted() -> None:
    """None ∩ None → None: both unrestricted compose to unrestricted."""
    result = BoundsSpec().intersect(BoundsSpec())
    assert result.tools is None
    assert result.permits_tool("anything") is True


def test_intersect_none_and_set_narrows_to_set() -> None:
    """None ∩ set → set: a task envelope narrows an unrestricted owl."""
    owl = BoundsSpec()  # unrestricted
    task = BoundsSpec(tools=frozenset({"read_file"}))
    result = owl.intersect(task)
    assert result.tools == frozenset({"read_file"})
    assert result.permits_tool("read_file") is True
    assert result.permits_tool("delete_all") is False


def test_intersect_set_and_none_keeps_owl_set() -> None:
    """set ∩ None → set: an unrestricted task never widens the owl's allowlist."""
    owl = BoundsSpec(tools=frozenset({"read_file"}))
    result = owl.intersect(BoundsSpec())
    assert result.tools == frozenset({"read_file"})


def test_intersect_set_and_set_narrows_to_overlap() -> None:
    """set ∩ set → intersection: only tools BOTH permit survive (can only REMOVE)."""
    owl = BoundsSpec(tools=frozenset({"read_file", "search_files", "edit"}))
    task = BoundsSpec(tools=frozenset({"read_file", "search_files"}))
    result = owl.intersect(task)
    assert result.tools == frozenset({"read_file", "search_files"})
    # The task can only TIGHTEN — it cannot ADD a tool the owl lacks.
    widen_attempt = owl.intersect(BoundsSpec(tools=frozenset({"read_file", "rm_rf"})))
    assert widen_attempt.tools == frozenset({"read_file"})
    assert widen_attempt.permits_tool("rm_rf") is False


def test_intersect_disjoint_sets_is_empty_deny_all() -> None:
    """Disjoint allowlists → frozenset() (present but empty = deny ALL)."""
    owl = BoundsSpec(tools=frozenset({"read_file"}))
    task = BoundsSpec(tools=frozenset({"write_file"}))
    result = owl.intersect(task)
    assert result.tools == frozenset()
    assert result.permits_tool("read_file") is False
    assert result.permits_tool("write_file") is False


def test_intersect_returns_frozen_copy_does_not_mutate_inputs() -> None:
    owl = BoundsSpec(tools=frozenset({"a", "b"}))
    task = BoundsSpec(tools=frozenset({"a"}))
    result = owl.intersect(task)
    # inputs unchanged (frozen models, model_copy)
    assert owl.tools == frozenset({"a", "b"})
    assert task.tools == frozenset({"a"})
    assert result is not owl and result is not task
    with pytest.raises(ValidationError):
        result.tools = frozenset()  # type: ignore[misc]


def test_intersect_keeps_self_other_axes_as_stub() -> None:
    """Other axes are stubs that keep self (never widen). Documented S1 behavior."""
    owl = BoundsSpec(
        tools=frozenset({"a"}),
        fs_read_roots=("/ws",),
        data_owner_id="owner-1",
    )
    task = BoundsSpec(tools=frozenset({"a"}), fs_read_roots=("/other",))
    result = owl.intersect(task)
    # tools narrows; other axes keep self (owl's own values) for now.
    assert result.fs_read_roots == ("/ws",)
    assert result.data_owner_id == "owner-1"
