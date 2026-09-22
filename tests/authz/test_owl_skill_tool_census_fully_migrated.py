"""Story 4.9, AC4 — "Given the census, when this story lands, then no owl,
skill or tool entry is pending migration."

``authz/state_change_census.py::COMMAND_TYPE_MIGRATIONS`` named 22 pending
4.9 entries before this story: ``owls.build``, ``owls.build_tool``, 8
``owl.*`` and 12 ``skill.*`` keys. This story consolidated
``owl.create``/``.edit``/``.rename``/``.retire`` onto the new
``owls.build.*`` types (their OLD keys are DELETED, not left pending — see
that module's own docstring on deleting vs. flipping) and flipped every
remaining 4.9-owned entry to ``status="migrated"``.

This is a FORWARD-ONLY, story-scoped check — the reverse direction (every
ledger entry is declared by a live tool/command) and the "not vacuous"
control both already live in
``test_state_change_census_has_no_stale_entries.py``; this file only proves
the 4.9 SLICE of the ledger is fully burned down.
"""

from __future__ import annotations

import pytest

from stackowl.authz.state_change_census import COMMAND_TYPE_MIGRATIONS

pytestmark = pytest.mark.tripwire

#: The 22 pending keys spec-4-9's own frontmatter/Boundaries named, split
#: into the 4 consolidated (now-deleted) keys and the 18 that flip in place.
_CONSOLIDATED_AWAY = frozenset({
    "owl.create", "owl.edit", "owl.rename", "owl.retire",
})
_REPLACED_BY = frozenset({
    "owls.build.create", "owls.build.edit", "owls.build.rename",
    "owls.build.retire",
    # New, internal-only undo/widening types this story ALSO adds beyond the
    # 1:1 consolidation (retire's undo, and the sole authority-widening path).
    "owls.build.restore", "owls.build.grant",
})
_FLIPPED_IN_PLACE = frozenset({
    "owls.build_tool",  # split below, not kept as one coarse key
    "skill.author_create", "skill.author_edit", "skill.author_patch",
    "skill.delete", "skill.set_enabled", "skill.synthesize", "skill.install",
    "skill.reload_index", "skill.set_pinned", "skill.dedupe",
    "skill.migrate_standard", "skill.restore_version",
    "owl.reset_dna", "owl.dna_restore", "owl.cancel_objective",
    "owl.merge_objective",
})
_BUILD_TOOL_SPLIT = frozenset({"owls.build_tool.create", "owls.build_tool.delete"})


def test_the_four_consolidated_owl_keys_are_deleted_not_left_pending() -> None:
    """owl.create/edit/rename/retire named the SAME real mutation
    owls.build.create/edit/rename/retire now names — ONE SHARED NAMESPACE
    (this module's own docstring) forbids keeping both declared."""
    still_present = _CONSOLIDATED_AWAY & frozenset(COMMAND_TYPE_MIGRATIONS)
    assert not still_present, (
        f"these consolidated owl.* keys must be DELETED (never left "
        f"status='pending' or re-added as 'migrated' duplicates): {sorted(still_present)}"
    )


def test_every_replacement_and_flipped_entry_is_migrated() -> None:
    checked = _REPLACED_BY | _FLIPPED_IN_PLACE - {"owls.build_tool"} | _BUILD_TOOL_SPLIT
    missing = sorted(t for t in checked if t not in COMMAND_TYPE_MIGRATIONS)
    assert not missing, f"expected ledger entries are missing entirely: {missing}"
    not_migrated = sorted(
        t for t in checked
        if t in COMMAND_TYPE_MIGRATIONS and COMMAND_TYPE_MIGRATIONS[t].status != "migrated"
    )
    assert not not_migrated, (
        f"these Story 4.9 command types are still status='pending': {not_migrated}"
    )


def test_no_owl_skill_or_tool_entry_is_pending() -> None:
    """AC4, literally: scan the WHOLE ledger for any owl/skill/owls.build*
    prefix that is still pending — not just the enumerated list above, so a
    future entry added under the same prefixes is caught too."""
    prefixes = ("owl.", "owls.build", "skill.")
    pending = sorted(
        key for key, mig in COMMAND_TYPE_MIGRATIONS.items()
        if key.startswith(prefixes) and mig.status == "pending"
    )
    assert not pending, f"owl/skill/tool entries still pending migration: {pending}"


def test_at_least_22_owl_skill_tool_entries_exist_and_are_migrated() -> None:
    """VACUITY CONTROL — proves the prefix scan above has a real denominator
    (spec-4-9's own frontmatter named exactly 22 pending entries)."""
    prefixes = ("owl.", "owls.build", "skill.")
    matching = [k for k in COMMAND_TYPE_MIGRATIONS if k.startswith(prefixes)]
    assert len(matching) >= 22, (
        f"expected at least 22 owl/skill/tool ledger entries, found {len(matching)}: "
        f"{sorted(matching)}"
    )
    assert all(COMMAND_TYPE_MIGRATIONS[k].status == "migrated" for k in matching)
