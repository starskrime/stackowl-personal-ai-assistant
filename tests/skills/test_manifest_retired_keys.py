"""D09.3 slice 5 — a retired frontmatter key must not take the catalog down.

``summary`` was removed from SkillManifest and from the database (migration
0110). 142 of the 169 SKILL.md files on disk still carried it at that moment,
and the model is ``extra="forbid"`` — so the obvious removal would have failed
84% of the catalog to load on the next boot.

The replacement for ``test_summarize_backfill.py``, which tested the generation
pass that no longer exists. These tests guard the compatibility seam that made
removing it safe, which is the part that can regress.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from stackowl.skills.manifest import SkillManifest


def test_a_legacy_summary_key_is_accepted_and_dropped():
    """THE test. Disabling most of the agent's skills as a side effect of a
    schema cleanup is not a loud failure, it is an outage."""
    m = SkillManifest(
        name="legacy", description="d", when_to_use="w",
        summary="a generated blurb from before slice 5",  # type: ignore[call-arg]
    )
    assert m.name == "legacy"
    assert not hasattr(m, "summary")


def test_a_typo_is_still_rejected():
    """extra="forbid" is right for a typo — it turns `descrption:` into a named
    error instead of a silently ignored key. Dropping RETIRED keys must not
    weaken that."""
    with pytest.raises(ValidationError):
        SkillManifest(
            name="typo", description="d", descrption="oops",  # type: ignore[call-arg]
        )


def test_the_drop_does_not_disturb_the_other_fields():
    m = SkillManifest(
        name="legacy", description="d", when_to_use="w", version="1.2.3",
        tags=["a"], summary="x",  # type: ignore[call-arg]
    )
    assert (m.version, m.tags, m.when_to_use) == ("1.2.3", ["a"], "w")


def test_a_manifest_without_the_legacy_key_is_untouched():
    m = SkillManifest(name="modern", description="d", when_to_use="w")
    assert m.name == "modern"
