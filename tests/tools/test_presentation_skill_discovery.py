"""Skill-discovery tools must survive the per-turn budget on weak models.

The default Secretary owl owns no skills; if `skills_list`/`skill_view` can be
pruned by the budgeter, a small-window model can never self-discover skills. They
belong in the guaranteed base set alongside the other skill tools (skill_manage)
— their omission was the bug.

NOTE ON `synthesize_skills`, which this file used to assert was guaranteed. It
was REMOVED from the base set by Bakir's decision on ESC-46, 2026-08-23. This
test was not relaxed to let a change pass — the invariant it pinned was
overturned by the person who set it, so the assertion is INVERTED rather than
deleted, and the tool's continued registration is pinned in its place.

The evidence behind that decision: zero calls in two independent sources (the
full retained JSONL window, and `side_effect_ledger` over 55 days / 1,752 rows),
while holding a guaranteed slot under a cap of ~36. Not unreachable — lane-less.
Its `parameters` are `{}` so it cannot be aimed at anything, and its own
description tells the model to use `skill_manage` instead.
"""

from __future__ import annotations

from stackowl.tools._infra.presentation import _DEFAULT_BASE
from stackowl.tools.registry import ToolRegistry


def test_discovery_tools_are_in_guaranteed_base() -> None:
    assert "skills_list" in _DEFAULT_BASE
    assert "skill_view" in _DEFAULT_BASE
    # Sanity: they sit alongside the authoring/learning skill tools already there.
    assert "skill_manage" in _DEFAULT_BASE


def test_synthesize_skills_is_NOT_guaranteed_but_IS_still_registered() -> None:
    """ESC-46. The capability must survive the loss of its guaranteed slot.

    Removing it from the base set is a PRESENTATION change only. If this ever
    fails on the second assertion, the tool itself was dropped — which is not
    what was decided, and is the regression to look for.
    """
    assert "synthesize_skills" not in _DEFAULT_BASE
    assert ToolRegistry.with_defaults().get("synthesize_skills") is not None


def test_self_extension_survives_the_removal() -> None:
    """The standing rule is that the assistant extends itself. That rule rests on
    these five, not on the tool that was removed."""
    for still_guaranteed in (
        "skill_manage", "tool_build", "owl_build", "reflect_now", "evolve_now",
    ):
        assert still_guaranteed in _DEFAULT_BASE, still_guaranteed
