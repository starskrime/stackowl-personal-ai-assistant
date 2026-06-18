"""E2-S2 — a task envelope may not silently narrow a not-yet-enforced axis."""

from __future__ import annotations

import pytest

from stackowl.authz import BoundsSpec
from stackowl.authz.enforcement import (
    ENFORCED_AXES,
    assert_task_narrowing_enforceable,
    unenforced_axis_change,
)
from stackowl.exceptions import DomainError

OWL = BoundsSpec(tools=frozenset({"a", "b"}))


def test_only_tools_enforced_today() -> None:
    assert ENFORCED_AXES == frozenset({"tools"})


def test_tools_narrowing_is_enforceable() -> None:
    task = BoundsSpec(tools=frozenset({"a"}))
    assert unenforced_axis_change(OWL, task) == set()
    assert_task_narrowing_enforceable(OWL, task)  # no raise


def test_ceiling_equal_to_owl_passes() -> None:
    assert_task_narrowing_enforceable(OWL, OWL)


def test_network_narrowing_is_refused() -> None:
    from stackowl.authz.bounds import NetworkRule

    task = BoundsSpec(tools=frozenset({"a"}), network=(NetworkRule(host="x"),))
    assert "network" in unenforced_axis_change(OWL, task)
    with pytest.raises(DomainError):
        assert_task_narrowing_enforceable(OWL, task)


def test_fs_narrowing_is_refused() -> None:
    task = BoundsSpec(tools=frozenset({"a"}), fs_read_roots=("/safe",))
    with pytest.raises(DomainError):
        assert_task_narrowing_enforceable(OWL, task)


def test_network_widening_is_also_refused() -> None:
    # An unenforced axis cannot be WIDENED either — any divergence is refused,
    # because no seam can honor the value in S2.
    from stackowl.authz.bounds import NetworkRule

    owl = BoundsSpec(tools=frozenset({"a"}), network=(NetworkRule(host="x"),))
    task = BoundsSpec(tools=frozenset({"a"}), network=(NetworkRule(host="x"), NetworkRule(host="y")))
    assert "network" in unenforced_axis_change(owl, task)
    with pytest.raises(DomainError):
        assert_task_narrowing_enforceable(owl, task)


def test_axis_unset_covers_all_bounds_fields() -> None:
    """_AXIS_UNSET must name every BoundsSpec field except caps; a new axis trips this."""
    from stackowl.authz.enforcement import _AXIS_UNSET

    all_axes = set(BoundsSpec.model_fields) - {"caps"}
    assert set(_AXIS_UNSET) == all_axes, (
        f"_AXIS_UNSET out of sync with BoundsSpec: "
        f"missing={all_axes - set(_AXIS_UNSET)}, extra={set(_AXIS_UNSET) - all_axes}"
    )
