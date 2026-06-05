"""E2-S2 — a task envelope may not silently narrow a not-yet-enforced axis."""

from __future__ import annotations

import pytest

from stackowl.authz import BoundsSpec
from stackowl.authz.enforcement import (
    ENFORCED_AXES,
    assert_task_narrowing_enforceable,
    unenforced_narrowing,
)
from stackowl.exceptions import DomainError

OWL = BoundsSpec(tools=frozenset({"a", "b"}))


def test_only_tools_enforced_today() -> None:
    assert ENFORCED_AXES == frozenset({"tools"})


def test_tools_narrowing_is_enforceable() -> None:
    task = BoundsSpec(tools=frozenset({"a"}))
    assert unenforced_narrowing(OWL, task) == set()
    assert_task_narrowing_enforceable(OWL, task)  # no raise


def test_ceiling_equal_to_owl_passes() -> None:
    assert_task_narrowing_enforceable(OWL, OWL)


def test_network_narrowing_is_refused() -> None:
    from stackowl.authz.bounds import NetworkRule

    task = BoundsSpec(tools=frozenset({"a"}), network=(NetworkRule(host="x"),))
    assert "network" in unenforced_narrowing(OWL, task)
    with pytest.raises(DomainError):
        assert_task_narrowing_enforceable(OWL, task)


def test_fs_narrowing_is_refused() -> None:
    task = BoundsSpec(tools=frozenset({"a"}), fs_read_roots=("/safe",))
    with pytest.raises(DomainError):
        assert_task_narrowing_enforceable(OWL, task)
