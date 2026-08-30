"""Deleting a skill must not leave its ownership rows behind.

MEASURED LIVE 2026-08-29 (post-purge state of the running platform)::

    skills               14 rows
    skill_ownership     111 rows
    ownership rows pointing at a skill that no longer exists:  111  (100%)
    [owls] hydrate_skill_ownership: exit  {'attached': 10}

So ten dead skill names are attached to live owls right now, and the other 101
rows are dropped per-row with no INFO line to say so.

THE DEFECT IS BUILT-BUT-NOT-WIRED, this repo's most common shape.
``purge_skill_ownership`` exists, is correct, and states the consequence of not
calling it in its own docstring: *"Without this a deprecated/deleted skill's rows
linger and the boot hydrator re-attaches a now-dead skill name to its owl forever
(phantom ownership)."* It is called from exactly one place — ``synthesizer.py`` —
and NOT from ``store.delete()``, which is the normal deletion path.

``delete()`` already gets the other dependent right: it removes the ``skills_fts``
row in the same transaction, with a comment explaining that the two must not
diverge. Ownership was simply missed. This makes the two consistent.

WHAT THIS IS NOT. It is not a fix for the 111 rows already on disk — that is data
deletion and belongs to the operator, not to an autonomous loop. It stops the next
deletion from adding more.

WHY THE FTS ORPHANS ARE NOT PART OF THIS. The live index holds 165 rows against 14
skills, but ``keyword_recall`` reads them through
``FROM skills_fts fts JOIN skills s ... WHERE s.enabled = 1``, so an orphan row can
never surface a dead skill. Checked rather than assumed — the reader is what decides
whether a stale write matters, and here it does not.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

from stackowl.db.pool import DbPool
from stackowl.skills.store import SkillIndexStore
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID
from tests._schema_template import seed_schema


@pytest.fixture()
async def pool(tmp_path: Path) -> AsyncGenerator[DbPool]:
    db_path = tmp_path / "skills.db"
    seed_schema(db_path)
    p = DbPool(db_path=db_path)
    await p.open()
    try:
        yield p
    finally:
        await p.close()


async def _own(pool: DbPool, owl: str, skill: str) -> None:
    """Written through the module's OWN insert SQL, not a hand-rolled copy.

    A fixture that builds its own INSERT drifts from the writer it stands in for —
    this suite already lost two rounds to exactly that (missing loaded_at, then
    updated_at). Borrowing the constant keeps the fixture honest by construction.
    """
    from stackowl.owls.skill_ownership import _UPSERT

    await pool.execute(
        _UPSERT, (DEFAULT_PRINCIPAL_ID, owl, skill, "2026-08-29T00:00:00+00:00")
    )


async def _ownership_rows(pool: DbPool, skill: str) -> int:
    rows = await pool.fetch_all(
        "SELECT 1 FROM skill_ownership WHERE owner_id = ? AND skill_name = ?",
        (DEFAULT_PRINCIPAL_ID, skill),
    )
    return len(rows)


async def _seed_skill(pool: DbPool, name: str) -> int:
    await pool.execute(
        "INSERT INTO skills (name, source, path, description, when_to_use, "
        "version, enabled, owner_id, loaded_at, updated_at) "
        "VALUES (?,?,?,?,?,?,1,?,?,?)",
        (name, "learned", str(Path("/tmp") / name), "d", "w", "1",
         DEFAULT_PRINCIPAL_ID, "2026-08-29T00:00:00+00:00",
         "2026-08-29T00:00:00+00:00"),
    )
    rows = await pool.fetch_all(
        "SELECT skill_id FROM skills WHERE name = ? AND owner_id = ?",
        (name, DEFAULT_PRINCIPAL_ID),
    )
    return int(rows[0]["skill_id"])


@pytest.mark.asyncio
async def test_delete_removes_the_skills_OWNERSHIP_rows(pool: DbPool) -> None:
    """The defect. Two owls own it; deleting the skill must strand neither."""
    skill_id = await _seed_skill(pool, "doomed-skill")
    await _own(pool, "secretary", "doomed-skill")
    await _own(pool, "rca_gatherer", "doomed-skill")
    assert await _ownership_rows(pool, "doomed-skill") == 2

    await SkillIndexStore(pool).delete(skill_id)

    assert await _ownership_rows(pool, "doomed-skill") == 0, (
        "the skill is gone and its ownership rows remain — the boot hydrator will "
        "re-attach a dead skill name to those owls on every boot (phantom ownership, "
        "measured live at 111 dangling rows / 10 attached)"
    )


@pytest.mark.asyncio
async def test_it_does_not_touch_ANOTHER_skills_ownership(pool: DbPool) -> None:
    """The guard must be narrow — this deletes one skill, not a table."""
    doomed = await _seed_skill(pool, "doomed-skill")
    await _seed_skill(pool, "keeper")
    await _own(pool, "secretary", "doomed-skill")
    await _own(pool, "secretary", "keeper")

    await SkillIndexStore(pool).delete(doomed)

    assert await _ownership_rows(pool, "keeper") == 1, "an unrelated skill was detached"


@pytest.mark.asyncio
async def test_deleting_an_UNOWNED_skill_is_a_clean_no_op(pool: DbPool) -> None:
    """Most skills are owned by nobody. That path must stay boring."""
    skill_id = await _seed_skill(pool, "lonely")
    await SkillIndexStore(pool).delete(skill_id)
    rows = await pool.fetch_all("SELECT 1 FROM skills WHERE skill_id = ?", (skill_id,))
    assert not rows


@pytest.mark.asyncio
async def test_the_fts_row_is_STILL_removed(pool: DbPool) -> None:
    """The dependent delete() already got right must not regress."""
    skill_id = await _seed_skill(pool, "indexed")
    await pool.execute(
        "INSERT INTO skills_fts (rowid, name, description, when_to_use) VALUES (?,?,?,?)",
        (skill_id, "indexed", "d", "w"),
    )
    await SkillIndexStore(pool).delete(skill_id)
    rows = await pool.fetch_all(
        "SELECT 1 FROM skills_fts WHERE rowid = ?", (skill_id,)
    )
    assert not rows, "the skills_fts row outlived its skill"
