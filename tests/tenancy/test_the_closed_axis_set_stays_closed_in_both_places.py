"""D17.6 — a sixth bounds axis added to the model and not to the refusal table
would be a task constraint nobody refuses and nobody enforces.

`BoundsSpec`'s docstring calls the enumeration "intentionally CLOSED — exactly
these five axes exist". `authz/enforcement.py` keeps a SECOND enumeration,
`_AXIS_UNSET`, listing every axis a task spec can carry, and walks it to decide
whether a task-scoped divergence must be refused:

    for axis, unset in _AXIS_UNSET.items():
        if axis in ENFORCED_AXES: continue
        ...
        changed.add(axis)

**An axis absent from `_AXIS_UNSET` is never walked.** It would not appear in
`unenforced_axis_change`, `assert_task_narrowing_enforceable` would not refuse
it, and a task could carry a constraint that no seam enforces and no check
rejects — which is precisely the "false confidence" that module exists to
prevent, arriving through the one door it does not watch.

Two enumerations of one closed set is `CLAUDE.md` shape 3. They agree today.
This asserts they keep agreeing, from the model rather than from a list.

MEASURED 2026-09-05, and the table is honest: `ENFORCED_AXES` is `{"tools"}`,
and of the other four —

    network                    NO reader of `BoundsSpec.network` anywhere.
                               (`execute_code`'s `.network` is a sandbox flag on
                               a different object.)
    fs_read_roots/_write_roots `path_guard` anchors to a global `data_root()`,
                               never to per-owl roots.
    data_owner_id/_namespaces  `OwnedRepository` constrains EVERY query to its
                               owner_id, so the axis is enforced globally — but
                               NOTHING plumbs a task-scoped value, so it cannot
                               be honoured as a per-task divergence. Both
                               statements in the tree are true; that distinction
                               is what they now say out loud.
"""

from __future__ import annotations

import pytest

from stackowl.authz import enforcement
from stackowl.authz.bounds import BoundsSpec

#: `caps` is deliberately outside the task-divergence table: a ResourceCaps object is
#: always present, and its enforcement is the budget governor's, not this seam's.
_NOT_TASK_SCOPED = {"caps"}


@pytest.mark.tripwire
def test_the_refusal_table_covers_every_axis_the_model_declares() -> None:
    model_axes = set(BoundsSpec.model_fields) - _NOT_TASK_SCOPED
    table_axes = set(enforcement._AXIS_UNSET)

    missing = model_axes - table_axes
    assert not missing, (
        f"BoundsSpec declares {sorted(missing)} but _AXIS_UNSET does not walk them. "
        "An axis absent from that table is never checked, so a task could carry a "
        "constraint no seam enforces and no check refuses — the exact false "
        "confidence enforcement.py exists to prevent."
    )

    stale = table_axes - model_axes
    assert not stale, (
        f"_AXIS_UNSET names {sorted(stale)}, which BoundsSpec no longer declares. "
        "A refusal table that outlives its model refuses nothing."
    )


@pytest.mark.tripwire
def test_every_enforced_axis_is_a_real_axis() -> None:
    unknown = enforcement.ENFORCED_AXES - set(BoundsSpec.model_fields)
    assert not unknown, (
        f"ENFORCED_AXES claims {sorted(unknown)}, which is not a BoundsSpec field. "
        "Claiming enforcement of an axis that does not exist is worse than "
        "claiming none."
    )


def test_the_enumeration_is_still_five_axes_plus_caps() -> None:
    """The docstring says CLOSED and names five. If a sixth arrives, the
    docstring, the refusal table and this count must move together — which is
    the point of it being closed."""
    assert len(set(BoundsSpec.model_fields) - _NOT_TASK_SCOPED) == 6, (
        "five AXES, six fields: fs_read_roots and fs_write_roots are one axis in "
        "the docstring's numbering, as are data_owner_id and data_namespaces"
    )


def test_an_unenforced_divergence_is_still_refused() -> None:
    """The behaviour the table protects, exercised rather than assumed.

    Both directions matter: a task that TIGHTENS an unenforced axis is refused
    too, because no seam can honour a narrowing any more than a widening.
    """
    owl = BoundsSpec()
    task = BoundsSpec(network=[])  # "no network at all" — nothing enforces it

    changed = enforcement.unenforced_axis_change(owl, task)
    assert "network" in changed

    with pytest.raises(Exception):
        enforcement.assert_task_narrowing_enforceable(owl, task)
