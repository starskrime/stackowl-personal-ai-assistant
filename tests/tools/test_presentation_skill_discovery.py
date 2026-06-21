"""Skill-discovery tools must survive the per-turn budget on weak models.

The default Secretary owl owns no skills; if `skills_list`/`skill_view` can be
pruned by the budgeter, a small-window model can never self-discover skills. They
belong in the guaranteed base set alongside the other skill tools (skill_manage,
synthesize_skills) — their omission was the bug.
"""

from __future__ import annotations

from stackowl.tools._infra.presentation import _DEFAULT_BASE


def test_discovery_tools_are_in_guaranteed_base() -> None:
    assert "skills_list" in _DEFAULT_BASE
    assert "skill_view" in _DEFAULT_BASE
    # Sanity: they sit alongside the authoring/learning skill tools already there.
    assert "skill_manage" in _DEFAULT_BASE
    assert "synthesize_skills" in _DEFAULT_BASE
