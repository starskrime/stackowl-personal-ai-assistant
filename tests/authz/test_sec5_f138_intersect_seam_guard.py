"""SEC-5 / F138 — intersect() fs/network/data narrowing is a no-op AT THE MATH LAYER,
but the seam fails CLOSED so the no-op can never manufacture false confidence.

The story's real narrowing on fs/network/data lands WITH the Epic-3 fs/network
enforcement seams. Until then this guard locks the two-part safety property:

1. ``intersect()`` keeps ``self`` on the unenforced axes — it NEVER widens (so the
   no-op is safe-by-direction).
2. ``assert_task_narrowing_enforceable`` REFUSES any task-scoped divergence on an
   axis no seam enforces — so a task can never *imply* an fs/network/data guarantee
   the math layer would silently drop.

If a future change wires an fs/network seam (adds to ``ENFORCED_AXES``) WITHOUT
making ``intersect`` narrow that axis, assertion (2) below for that axis will start
passing through and this test must be updated in lockstep — exactly the
"land real narrowing in the same change that wires the seam" contract.
"""

from __future__ import annotations

import pytest

from stackowl.authz.bounds import BoundsSpec, NetworkRule
from stackowl.authz.enforcement import (
    ENFORCED_AXES,
    assert_task_narrowing_enforceable,
)
from stackowl.exceptions import DomainError


def test_intersect_never_widens_unenforced_axes() -> None:
    owl = BoundsSpec(
        tools=frozenset({"a"}),
        fs_read_roots=("/ws",),
        fs_write_roots=("/ws",),
        network=(NetworkRule(host="allowed"),),
        data_owner_id="owner-1",
        data_namespaces=("ns-a",),
    )
    # A task that TRIES to broaden every unenforced axis.
    task = BoundsSpec(
        tools=frozenset({"a"}),
        fs_read_roots=("/ws", "/etc"),
        network=(NetworkRule(host="allowed"), NetworkRule(host="evil")),
        data_owner_id="owner-2",
        data_namespaces=("ns-a", "ns-b"),
    )
    result = owl.intersect(task)
    # The math layer keeps SELF — the task's broader values are ignored (never widen).
    assert result.fs_read_roots == ("/ws",)
    assert result.fs_write_roots == ("/ws",)
    assert result.network == (NetworkRule(host="allowed"),)
    assert result.data_owner_id == "owner-1"
    assert result.data_namespaces == ("ns-a",)


@pytest.mark.parametrize(
    "axis_kwargs",
    [
        {"fs_read_roots": ("/safe",)},
        {"fs_write_roots": ("/safe",)},
        {"network": (NetworkRule(host="x"),)},
        {"data_owner_id": "owner-x"},
        {"data_namespaces": ("ns-x",)},
    ],
)
def test_task_divergence_on_unenforced_axis_is_refused(axis_kwargs: dict) -> None:
    owl = BoundsSpec(tools=frozenset({"a"}))
    task = BoundsSpec(tools=frozenset({"a"}), **axis_kwargs)
    # The diverging axis is NOT yet enforced → fail closed at construction.
    assert all(axis not in ENFORCED_AXES for axis in axis_kwargs)
    with pytest.raises(DomainError):
        assert_task_narrowing_enforceable(owl, task)


def test_only_tools_axis_enforced_today() -> None:
    # The premise of F138's safety net: the math no-op is acceptable ONLY while
    # these axes are unenforced. Wiring a seam must land real narrowing too.
    assert frozenset({"tools"}) == ENFORCED_AXES
