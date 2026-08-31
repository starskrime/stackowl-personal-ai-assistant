"""Deleting a skill removes its files too — inside the skills root, never a builtin.

Bakir, 2026-08-31: "one delete removes the skill entirely", and when asked which
failure he preferred if a crash lands between the two halves, "database rows
first, then the directory".

WHY BOTH HALVES. ``SkillStore.delete`` already removed ``skills`` + ``skills_fts``
+ ``skill_ownership`` in one transaction, and its docstring said "File system
deletion is the caller's job." That is how a job gets done by nobody: measured
2026-08-31, ``~/.stackowl/skills/learned`` held 16 directories against 6 database
rows — 10 of them empty shells.

WHY ROWS FIRST. A crash between the halves then leaves an orphan DIRECTORY, which
we measured doing no harm at all — the 10 shells held no SKILL.md and nothing
loaded them. The other order leaves a database row pointing at content that no
longer exists: a skill the catalogue offers the model and that can never load.

THE PATH COMES FROM A DATABASE COLUMN, so it is data, not a constant. Two guards
follow from that, and neither is optional:

* CONFINEMENT — a path outside the skills root is never removed. ``rmtree`` on an
  attacker- or bug-supplied absolute path is unrecoverable, and this codebase has
  already paid once for trusting a data-supplied filename (the audit-recovery
  export, same day).
* BUILTINS ARE LEFT ALONE — they are seeded idempotently from a packaged
  directory on every boot (``loader.load_all(builtin_seed_dir=...)``), so removing
  one is churn that the next restart silently undoes, which reads as a delete that
  did not work.
"""

from __future__ import annotations

import pytest

from stackowl.db.pool import DbPool
from stackowl.paths import StackowlHome
from stackowl.skills.store import SkillIndexStore
from tests._schema_template import seed_schema

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def store(tmp_path, monkeypatch):  # noqa: ANN001, ANN201
    """A closed pool per test — an unclosed one hangs the run (learned 2026-08-31)."""
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path))
    path = tmp_path / "skills.db"
    pool = DbPool(db_path=path)
    await pool.open()
    seed_schema(path)
    yield SkillIndexStore(pool), pool
    await pool.close()


async def _insert(pool: DbPool, name: str, source: str, path: str) -> int:
    await pool.execute(
        "INSERT INTO skills (name, source, path, description, when_to_use, body_text,"
        " loaded_at, updated_at) VALUES (?, ?, ?, 'd', 'w', 'b', 1000.0, 1000.0)",
        (name, source, path),
    )
    rows = await pool.fetch_all("SELECT skill_id FROM skills WHERE name = ?", (name,))
    return int(rows[0]["skill_id"])


# =========================================================================== #
# 1. The files go
# =========================================================================== #


async def test_deleting_a_learned_skill_removes_its_directory(store) -> None:  # noqa: ANN001
    index, pool = store
    skill_dir = StackowlHome.skills_dir() / "learned" / "doomed"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# doomed", encoding="utf-8")
    skill_id = await _insert(pool, "doomed", "learned", str(skill_dir))

    await index.delete(skill_id)

    assert not skill_dir.exists(), "the row went and the files stayed"


async def test_the_rows_go_too(store) -> None:  # noqa: ANN001
    """The pre-existing behaviour, asserted so the disk leg cannot quietly replace it."""
    index, pool = store
    skill_dir = StackowlHome.skills_dir() / "learned" / "doomed"
    skill_dir.mkdir(parents=True)
    skill_id = await _insert(pool, "doomed", "learned", str(skill_dir))

    await index.delete(skill_id)

    rows = await pool.fetch_all("SELECT COUNT(*) AS c FROM skills WHERE name = 'doomed'")
    assert int(rows[0]["c"]) == 0


# =========================================================================== #
# 2. The guards
# =========================================================================== #


async def test_a_builtin_directory_is_left_alone(store) -> None:  # noqa: ANN001
    """Builtins are re-seeded from a packaged directory on every boot, so removing
    one is churn the next restart undoes."""
    index, pool = store
    skill_dir = StackowlHome.skills_dir() / "builtin" / "shipped"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# shipped", encoding="utf-8")
    skill_id = await _insert(pool, "shipped", "builtin", str(skill_dir))

    await index.delete(skill_id)

    assert skill_dir.exists(), "a platform-shipped skill directory was removed"
    rows = await pool.fetch_all("SELECT COUNT(*) AS c FROM skills WHERE name = 'shipped'")
    assert int(rows[0]["c"]) == 0, "the row should still go"


async def test_a_path_outside_the_skills_root_is_never_removed(
    store, tmp_path,  # noqa: ANN001
) -> None:
    """The path is a DATABASE COLUMN. rmtree on a data-supplied absolute path is
    unrecoverable, and this codebase already paid once for trusting one."""
    index, pool = store
    outside = tmp_path / "not-a-skill"
    outside.mkdir(parents=True)
    (outside / "precious.txt").write_text("keep me", encoding="utf-8")
    skill_id = await _insert(pool, "escapee", "learned", str(outside))

    await index.delete(skill_id)

    assert outside.exists(), "a directory outside the skills root was deleted"
    assert (outside / "precious.txt").is_file()


async def test_a_traversal_path_is_never_removed(store, tmp_path) -> None:  # noqa: ANN001
    index, pool = store
    outside = tmp_path / "sibling"
    outside.mkdir(parents=True)
    sneaky = str(StackowlHome.skills_dir() / "learned" / ".." / ".." / ".." / "sibling")
    skill_id = await _insert(pool, "sneaky", "learned", sneaky)

    await index.delete(skill_id)

    assert outside.exists(), "a ../ path escaped the skills root"


# =========================================================================== #
# 3. It can never cost the delete
# =========================================================================== #


async def test_a_missing_directory_is_a_clean_no_op(store) -> None:  # noqa: ANN001
    index, pool = store
    skill_dir = StackowlHome.skills_dir() / "learned" / "never-existed"
    skill_id = await _insert(pool, "ghost", "learned", str(skill_dir))

    await index.delete(skill_id)  # must not raise

    rows = await pool.fetch_all("SELECT COUNT(*) AS c FROM skills WHERE name = 'ghost'")
    assert int(rows[0]["c"]) == 0


async def test_an_empty_path_is_a_clean_no_op(store) -> None:  # noqa: ANN001
    """The column predates the disk leg, so a row may carry no path at all."""
    index, pool = store
    skill_id = await _insert(pool, "pathless", "learned", "")

    await index.delete(skill_id)  # must not raise

    rows = await pool.fetch_all("SELECT COUNT(*) AS c FROM skills WHERE name = 'pathless'")
    assert int(rows[0]["c"]) == 0
