"""enforcement — which BoundsSpec axes a dispatch seam actually ENFORCES (E2-S2).

A BoundsSpec models five axes; in S2 only TOOLS is enforced (at the dispatch
seam). The other four are modeled for Epic 3+. A *task envelope* that narrows an
axis no seam enforces would manufacture false confidence (e.g. ``network: none``
that does not block the network). So a task-scoped narrowing of an unenforced
axis is REFUSED at construction — fail closed. The creation_ceiling (a copy of
the owl's own bounds) narrows nothing relative to the owl, so it always passes.

ENFORCED_AXES grows as Epic 3 wires the fs/network seams; nothing else changes.
"""

from __future__ import annotations

from stackowl.authz.bounds import BoundsSpec
from stackowl.exceptions import DomainError

#: Axes with a live enforcement seam. TOOLS only, in S2.
ENFORCED_AXES = frozenset({"tools"})

#: All axes a task spec can carry, paired with their "unset / unrestricted" value.
_AXIS_UNSET: dict[str, object] = {
    "tools": None,
    "fs_read_roots": None,
    "fs_write_roots": None,
    "network": None,
    "data_owner_id": None,
    "data_namespaces": None,
}


def unenforced_narrowing(owl: BoundsSpec | None, task: BoundsSpec) -> set[str]:
    """Return the unenforced axes on which ``task`` is stricter than ``owl``.

    An axis is "narrowed" when the task sets a non-unset value that differs from
    the owl's value on that axis. ``caps`` is excluded (a ResourceCaps object is
    always present; its enforcement is E2-S4/S5 and is handled there).
    """
    narrowed: set[str] = set()
    for axis, unset in _AXIS_UNSET.items():
        if axis in ENFORCED_AXES:
            continue
        task_val = getattr(task, axis)
        if task_val == unset:
            continue  # task does not constrain this axis
        owl_val = getattr(owl, axis) if owl is not None else None
        if task_val != owl_val:
            narrowed.add(axis)
    return narrowed


def assert_task_narrowing_enforceable(owl: BoundsSpec | None, task: BoundsSpec) -> None:
    """Raise DomainError if ``task`` narrows any axis no seam enforces (fail closed)."""
    bad = unenforced_narrowing(owl, task)
    if bad:
        raise DomainError(
            "task envelope narrows axes with no enforcement seam "
            f"({sorted(bad)}); refusing to imply a guarantee that is not enforced "
            "(only these axes are enforced today: " + ", ".join(sorted(ENFORCED_AXES)) + ")"
        )
