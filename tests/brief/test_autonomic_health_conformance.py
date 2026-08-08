"""D10.2 slice 6 — does the brief say whether the standard is holding?

THE FEEDBACK LEG. The validator refuses a non-conforming write, but a refusal
only covers skills authored since it shipped, and D10.2's acceptance is "zero
new non-conforming skills" — a claim about a trend, not a state. Without a
number in the brief, "the standard is enforced" is an assumption about code
rather than an observation of the catalog.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stackowl.brief.assemblers import AutonomicHealthAssembler, BriefContext
from stackowl.config.settings import Settings
from stackowl.skills import standard
from stackowl.skills.loader import LoadedSkill
from stackowl.skills.manifest import SkillManifest
from stackowl.skills.store import SkillIndexStore

pytestmark = pytest.mark.asyncio


async def _add(store: SkillIndexStore, name: str, *, description: str = "d") -> int:
    return await store.upsert(LoadedSkill(
        manifest=SkillManifest(
            name=name, description=description, when_to_use="w", source="learned",
        ),
        path=Path("/tmp/x") / name, body="b", tools_registered=0, owls_registered=0,
    ))


async def _items(tmp_db) -> list[str]:
    section = await AutonomicHealthAssembler(SkillIndexStore(tmp_db), tmp_db).assemble(
        BriefContext(job_id="brief-1", last_brief_time=None, settings=Settings()),
    )
    return section.items


async def test_a_conforming_catalog_reports_zero_violations(tmp_db):
    store = SkillIndexStore(tmp_db)
    await _add(store, "clean_skill", description="Fetch a page.")

    line = next(i for i in await _items(tmp_db) if i.startswith("skill_standard"))

    assert "numbered:0/1" in line
    assert f"over_{standard.MAX_DESCRIPTION_CHARS}_chars:0/1" in line


async def test_a_numbered_name_is_counted_and_called_a_defect(tmp_db):
    """A numbered name existing AFTER D10.2 means some write path reached the
    store without going through the validator. That is a defect, not a backlog
    item, and the brief has to say so rather than print a tidy statistic."""
    store = SkillIndexStore(tmp_db)
    await _add(store, "leaked_skill-3", description="Short.")

    items = await _items(tmp_db)

    assert any("numbered:1/1" in i for i in items)
    assert any("bypassing the validator" in i for i in items)


async def test_a_three_digit_suffix_is_still_caught(tmp_db):
    """The regression this check was rewritten to avoid. An earlier version used
    a SQL GLOB, which cannot express "one or more digits" without enumerating
    widths — so `foo-123` would have quietly passed the very check whose purpose
    is to notice it. The brief asks standard.validate_name instead."""
    store = SkillIndexStore(tmp_db)
    await _add(store, "leaked_skill-123", description="Short.")

    assert any("numbered:1/1" in i for i in await _items(tmp_db))


async def test_an_over_long_description_is_counted(tmp_db):
    store = SkillIndexStore(tmp_db)
    await _add(store, "wordy_skill", description="x" * (standard.MAX_DESCRIPTION_CHARS + 1))

    line = next(i for i in await _items(tmp_db) if i.startswith("skill_standard"))

    assert f"over_{standard.MAX_DESCRIPTION_CHARS}_chars:1/1" in line
    assert "numbered:0/1" in line


async def test_archived_skills_are_not_counted_against_conformance(tmp_db):
    """An archived skill is not offered, so holding it against the standard
    would report a debt that costs nothing and can never be paid down."""
    store = SkillIndexStore(tmp_db)
    skill_id = await _add(store, "old_skill-9", description="Short.")
    await store.set_lifecycle_state(skill_id, "archived", 0.0)
    await _add(store, "clean_skill", description="Short.")

    line = next(i for i in await _items(tmp_db) if i.startswith("skill_standard"))

    assert "numbered:0/1" in line


async def test_the_line_names_the_standard_version(tmp_db):
    """So a conformance number read six months from now can be told apart from
    one measured under a different set of rules (R6Q24)."""
    store = SkillIndexStore(tmp_db)
    await _add(store, "clean_skill", description="Short.")

    line = next(i for i in await _items(tmp_db) if i.startswith("skill_standard"))

    assert f"v{standard.STANDARD_VERSION}" in line
