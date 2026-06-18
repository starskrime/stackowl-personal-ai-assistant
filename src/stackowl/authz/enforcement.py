"""enforcement — which BoundsSpec axes a dispatch seam actually ENFORCES (E2-S2).

A BoundsSpec models five axes; in S2 only TOOLS is enforced (at the dispatch
seam). The other four are modeled for Epic 3+. A *task envelope* that diverges
on an axis no seam enforces would manufacture false confidence (e.g.
``network: none`` that does not block the network). So a task-scoped divergence
on an unenforced axis is REFUSED at construction — fail closed — whether the
task value is tighter OR looser than the owl's value, because no seam can honor
either. The creation_ceiling (a copy of the owl's own bounds) diverges on
nothing relative to the owl, so it always passes.

ENFORCED_AXES grows as Epic 3 wires the fs/network seams; nothing else changes.
"""

from __future__ import annotations

from stackowl.authz.bounds import BoundsSpec
from stackowl.exceptions import DomainError

#: Axes with a live enforcement seam. TOOLS only, in S2.
ENFORCED_AXES = frozenset({"tools"})

#: All axes a task spec can carry, paired with their "unset / unrestricted" value.
# ``caps`` is intentionally excluded: ResourceCaps is always-present; its
# enforcement is handled separately in E2-S4/S5.
_AXIS_UNSET: dict[str, object] = {
    "tools": None,
    "fs_read_roots": None,
    "fs_write_roots": None,
    "network": None,
    "data_owner_id": None,
    "data_namespaces": None,
}


def unenforced_axis_change(owl: BoundsSpec | None, task: BoundsSpec) -> set[str]:
    """Return the unenforced axes on which ``task`` DIFFERS from ``owl`` in ANY way.

    For an axis no seam enforces, neither a tighter nor a looser value can be
    honored — no downstream component will act on the constraint. So ANY
    divergence (narrowing OR widening) is refused; this function returns the set
    of such axes so the caller can fail closed. ``caps`` is excluded (a
    ResourceCaps object is always present; its enforcement is E2-S4/S5 and is
    handled there).
    """
    changed: set[str] = set()
    for axis, unset in _AXIS_UNSET.items():
        if axis in ENFORCED_AXES:
            continue
        task_val = getattr(task, axis)
        if task_val == unset:
            continue  # task does not constrain this axis
        owl_val = getattr(owl, axis) if owl is not None else None
        if task_val != owl_val:
            changed.add(axis)
    return changed


def assert_task_narrowing_enforceable(owl: BoundsSpec | None, task: BoundsSpec) -> None:
    """Raise DomainError if the task diverges on any axis no seam enforces (fail closed)."""
    bad = unenforced_axis_change(owl, task)
    if bad:
        raise DomainError(
            "task envelope constrains axes with no enforcement seam "
            f"({sorted(bad)}); refusing to imply a guarantee that is not enforced "
            "(only these axes are enforced today: " + ", ".join(sorted(ENFORCED_AXES)) + ")"
        )
