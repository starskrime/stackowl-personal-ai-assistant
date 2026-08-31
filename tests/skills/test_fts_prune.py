"""The keyword index must not offer skills that no longer exist.

Measured 2026-08-31 on the live database: ``skills`` held 28 rows and
``skills_fts`` held 179, of which 147 named a skill that is gone. Skill search
could surface and rank ``jarvis_warm_greeting``, ``download-youtube-video`` and
``the-chiefs-dashboard`` — none of which exist.

WHAT IT IS NOT. Measuring in BOTH directions found ZERO skills missing from the
index, so the write side is healthy, and both writers (``_sync_fts`` and
migration 0081) set ``rowid`` explicitly, so alignment is not the defect either.
The residue is historical: the 2026-08-30 purge removed 151 skills with raw SQL,
bypassing ``SkillStore.delete`` and therefore its FTS cleanup. 151 purged, 147
stale — the arithmetic matches.

WHICH IS WHY THIS IS A PRUNE, NOT A REWRITE OF THE SYNC. The bypass that caused
it was closed the same morning by the shell DML guard, and the delete path
already removes the index row. What was missing is anything that removes a row
whose skill went away by some other means. Third occurrence of this shape —
``committed_facts_fts`` indexed 1,112 dead rows before it.

DELETE BY ROWID, ALWAYS. FTS5's supported delete form is by rowid; a general
predicate against a virtual table is how an FTS index gets corrupted. The rowids
are read first, then removed.
"""

from __future__ import annotations

import pytest

from stackowl.db.pool import DbPool
from stackowl.skills.store import SkillIndexStore
from tests._schema_template import seed_schema

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def store(tmp_path, monkeypatch):  # noqa: ANN001, ANN201
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path))
    path = tmp_path / "fts.db"
    pool = DbPool(db_path=path)
    await pool.open()
    seed_schema(path)
    yield SkillIndexStore(pool), pool
    await pool.close()


async def _skill(pool: DbPool, name: str) -> int:
    await pool.execute(
        "INSERT INTO skills (name, source, path, description, when_to_use, body_text,"
        " loaded_at, updated_at) VALUES (?, 'learned', '/p', 'd', 'w', 'b', 1.0, 1.0)",
        (name,),
    )
    rows = await pool.fetch_all("SELECT skill_id FROM skills WHERE name = ?", (name,))
    return int(rows[0]["skill_id"])


async def _fts(pool: DbPool, rowid: int, name: str) -> None:
    await pool.execute(
        "INSERT INTO skills_fts (rowid, name, description, when_to_use)"
        " VALUES (?, ?, 'd', 'w')",
        (rowid, name),
    )


async def _count(pool: DbPool, sql: str) -> int:
    rows = await pool.fetch_all(sql)
    return int(rows[0]["c"])


async def test_an_index_row_for_a_deleted_skill_is_pruned(store) -> None:  # noqa: ANN001
    index, pool = store
    live = await _skill(pool, "alive")
    await _fts(pool, live, "alive")
    await _fts(pool, 9001, "long-gone")  # no skills row

    pruned = await index.prune_fts()

    assert pruned == 1
    assert await _count(
        pool, "SELECT COUNT(*) AS c FROM skills_fts WHERE name = 'long-gone'"
    ) == 0


async def test_a_live_skill_is_never_pruned(store) -> None:  # noqa: ANN001
    index, pool = store
    live = await _skill(pool, "alive")
    await _fts(pool, live, "alive")

    pruned = await index.prune_fts()

    assert pruned == 0
    assert await _count(
        pool, "SELECT COUNT(*) AS c FROM skills_fts WHERE name = 'alive'"
    ) == 1


async def test_an_empty_skills_table_prunes_NOTHING(store) -> None:  # noqa: ANN001
    """"An empty table is a QUESTION, not an answer."

    With zero skills every index row looks orphaned. Emptying the whole index on
    what is far more likely a mid-migration or broken read is the same failure
    the orphan sweep refuses.
    """
    _index, pool = store
    await _fts(pool, 1, "a")
    await _fts(pool, 2, "b")

    pruned = await _index.prune_fts()

    assert pruned == 0
    assert await _count(pool, "SELECT COUNT(*) AS c FROM skills_fts") == 2


async def test_it_is_idempotent(store) -> None:  # noqa: ANN001
    index, pool = store
    live = await _skill(pool, "alive")
    await _fts(pool, live, "alive")
    await _fts(pool, 9001, "long-gone")

    assert await index.prune_fts() == 1
    assert await index.prune_fts() == 0


async def test_it_never_raises_on_a_broken_index(store, monkeypatch) -> None:  # noqa: ANN001
    """A failed prune must not cost boot — the index being stale is survivable,
    the platform not starting is not."""
    index, pool = store
    await _skill(pool, "alive")

    async def _boom(*a: object, **k: object) -> list[dict]:
        raise RuntimeError("index is corrupt")

    monkeypatch.setattr(pool, "fetch_all", _boom)
    assert await index.prune_fts() == 0
