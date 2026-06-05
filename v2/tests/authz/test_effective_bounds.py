"""E2-S2 — effective_bounds(): total, narrowing-only fold of N optional specs."""

from __future__ import annotations

import pytest

from stackowl.authz import BoundsSpec
from stackowl.authz.bounds_guard import check_effective_bounds, effective_bounds

A = BoundsSpec(tools=frozenset({"a"}))
AB = BoundsSpec(tools=frozenset({"a", "b"}))
B = BoundsSpec(tools=frozenset({"b"}))
UNRESTRICTED = BoundsSpec(tools=None)


def test_no_args_is_none() -> None:
    assert effective_bounds() is None


def test_all_none_is_none() -> None:
    assert effective_bounds(None, None) is None


def test_single_arg_is_identity() -> None:
    # CRITICAL: the back-compat wrapper relies on this — a single spec is unchanged.
    assert effective_bounds(AB) == AB


def test_none_skipped() -> None:
    assert effective_bounds(None, AB, None) == AB


def test_intersection_narrows() -> None:
    assert effective_bounds(AB, A).tools == frozenset({"a"})


def test_cannot_widen() -> None:
    assert effective_bounds(A, B).tools == frozenset()


def test_disjoint_is_deny_all_not_union() -> None:
    eff = effective_bounds(A, B)
    assert eff.tools == frozenset()
    assert eff.tools is not None


def test_unrestricted_term_does_not_widen() -> None:
    assert effective_bounds(A, UNRESTRICTED).tools == frozenset({"a"})


@pytest.mark.parametrize(
    "eff,tool,permitted",
    [
        (None, "anything", True),
        (BoundsSpec(tools=frozenset({"a"})), "a", True),
        (BoundsSpec(tools=frozenset({"a"})), "b", False),
        (BoundsSpec(tools=frozenset()), "a", False),
    ],
)
def test_check_effective_bounds(eff: BoundsSpec | None, tool: str, permitted: bool) -> None:
    block = check_effective_bounds(eff, tool)
    assert (block is None) == permitted
