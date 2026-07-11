"""skills_fts sync-on-write (Story LAT.2, Task 2) — upsert/set_summary/delete
must keep skills_fts in lockstep with the skills row, mirroring
committed_facts_fts's application-layer sync (sqlite_bridge.py)."""

from __future__ import annotations

from pathlib import Path

import pytest

from stackowl.db.pool import DbPool
from stackowl.skills.loader import LoadedSkill
from stackowl.skills.manifest import SkillManifest
from stackowl.skills.store import SkillIndexStore


def _loaded(name="alpha", description="a description", when_to_use="when to use it", summary=None):
    return LoadedSkill(
        manifest=SkillManifest(
            name=name, description=description, when_to_use=when_to_use,
            source="user", summary=summary,
        ),
        path=Path("/tmp/x"), body="body", tools_registered=0,
        owls_registered=0, tool_names=(),
    )


async def _fts_row(db: DbPool, skill_id: int) -> dict | None:
    rows = await db.fetch_all(
        "SELECT rowid, name, description, when_to_use, summary FROM skills_fts WHERE rowid = ?",
        (skill_id,),
    )
    return rows[0] if rows else None


@pytest.mark.asyncio
async def test_upsert_makes_skill_findable_via_fts_match(tmp_db: DbPool):
    store = SkillIndexStore(tmp_db)
    skill_id = await store.upsert(_loaded(name="alpha", description="alpha description mentions widgets"))
    rows = await tmp_db.fetch_all(
        "SELECT rowid FROM skills_fts WHERE skills_fts MATCH ?", ("widgets",)
    )
    assert [r["rowid"] for r in rows] == [skill_id]


@pytest.mark.asyncio
async def test_set_summary_updates_fts_row(tmp_db: DbPool):
    store = SkillIndexStore(tmp_db)
    skill_id = await store.upsert(_loaded(name="alpha"))
    row = await _fts_row(tmp_db, skill_id)
    assert row is not None and row["summary"] == ""

    await store.set_summary(skill_id, "GENERATED PLAYBOOK about frobnicators", "generated", "hash1")

    row = await _fts_row(tmp_db, skill_id)
    assert row is not None
    assert row["summary"] == "GENERATED PLAYBOOK about frobnicators"
    rows = await tmp_db.fetch_all(
        "SELECT rowid FROM skills_fts WHERE skills_fts MATCH ?", ("frobnicators",)
    )
    assert [r["rowid"] for r in rows] == [skill_id]


@pytest.mark.asyncio
async def test_upsert_refresh_does_not_clobber_generated_summary_in_fts(tmp_db: DbPool):
    """A reboot re-scan (upsert with no author summary) must not wipe the
    generated summary already reflected in skills_fts."""
    store = SkillIndexStore(tmp_db)
    skill_id = await store.upsert(_loaded(name="alpha"))
    await store.set_summary(skill_id, "GENERATED PLAYBOOK", "generated", "hash1")

    await store.upsert(_loaded(name="alpha"))  # reboot re-scan, no author summary

    row = await _fts_row(tmp_db, skill_id)
    assert row is not None and row["summary"] == "GENERATED PLAYBOOK"


@pytest.mark.asyncio
async def test_delete_removes_fts_row(tmp_db: DbPool):
    store = SkillIndexStore(tmp_db)
    skill_id = await store.upsert(_loaded(name="alpha"))
    assert await _fts_row(tmp_db, skill_id) is not None

    await store.delete(skill_id)

    assert await _fts_row(tmp_db, skill_id) is None
