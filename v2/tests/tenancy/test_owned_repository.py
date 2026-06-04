"""OwnedRepository — cross-owner isolation + auto-stamping (Pass 1)."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

from stackowl.db.pool import DbPool
from stackowl.tenancy.owned_repository import OwnedRepository


class _WidgetRepo(OwnedRepository):
    """Tiny concrete subclass over a ``widgets(owner_id, name)`` table."""

    _table = "widgets"

    async def add(self, name: str) -> None:
        await self._insert_owned(self._table, {"name": name})

    async def names(self) -> list[str]:
        rows = await self._fetch_owned(self._table)
        return sorted(str(r["name"]) for r in rows)

    async def rename(self, old_name: str, new_name: str) -> None:
        """Owner-scoped UPDATE — uses _execute_owned with correct owner_id predicate."""
        await self._execute_owned(
            "UPDATE widgets SET name = ? WHERE owner_id = ? AND name = ?",
            (new_name, self._owner_id, old_name),
        )


@pytest.fixture()
async def pool(tmp_path: Path) -> AsyncGenerator[DbPool]:
    db_path = tmp_path / "widgets.db"
    p = DbPool(db_path=db_path)
    await p.open()
    await p.execute(
        "CREATE TABLE widgets (owner_id TEXT NOT NULL, name TEXT NOT NULL)"
    )
    try:
        yield p
    finally:
        await p.close()


async def test_fetch_owned_isolates_by_owner(pool: DbPool) -> None:
    alice = _WidgetRepo(pool, "principal-alice")
    bob = _WidgetRepo(pool, "principal-bob")

    await alice.add("a1")
    await alice.add("a2")
    await bob.add("b1")

    assert await alice.names() == ["a1", "a2"]
    assert await bob.names() == ["b1"]


async def test_insert_owned_stamps_owner_id(pool: DbPool) -> None:
    repo = _WidgetRepo(pool, "principal-alice")
    await repo.add("a1")

    rows = await pool.fetch_all("SELECT owner_id, name FROM widgets")
    assert rows == [{"owner_id": "principal-alice", "name": "a1"}]


async def test_fetch_owned_with_where_clause(pool: DbPool) -> None:
    alice = _WidgetRepo(pool, "principal-alice")
    await alice.add("keep")
    await alice.add("drop")
    # owner_id is bound first param the where param fills the named filter.
    rows = await alice._fetch_owned("widgets", "name = ?", ("keep",))
    assert [r["name"] for r in rows] == ["keep"]


async def test_insert_owned_rejects_owner_mismatch(pool: DbPool) -> None:
    repo = _WidgetRepo(pool, "principal-alice")
    with pytest.raises(ValueError, match="owner_id mismatch"):
        await repo._insert_owned("widgets", {"name": "x", "owner_id": "principal-bob"})


async def test_missing_table_fails_loud(pool: DbPool) -> None:
    class _NoTable(OwnedRepository):
        pass

    with pytest.raises(ValueError, match="_table"):
        _NoTable(pool, "principal-alice")


async def test_empty_owner_fails_loud(pool: DbPool) -> None:
    with pytest.raises(ValueError, match="owner_id"):
        _WidgetRepo(pool, "")


def test_owner_id_property(pool: DbPool) -> None:
    repo = _WidgetRepo(pool, "principal-zed")
    assert repo.owner_id == "principal-zed"


async def test_execute_owned_scoped_update_leaves_other_owner_untouched(
    pool: DbPool,
) -> None:
    """_execute_owned with a correct owner_id predicate only touches the caller's rows."""
    alice = _WidgetRepo(pool, "principal-alice")
    bob = _WidgetRepo(pool, "principal-bob")

    await alice.add("a1")
    await bob.add("b1")

    await alice.rename("a1", "a1-renamed")

    assert await alice.names() == ["a1-renamed"]
    assert await bob.names() == ["b1"]  # bob's row is untouched


async def test_execute_owned_without_owner_id_predicate_raises(pool: DbPool) -> None:
    """_execute_owned must raise ValueError when SQL omits the owner_id predicate."""
    alice = _WidgetRepo(pool, "principal-alice")
    with pytest.raises(ValueError, match="owner_id predicate"):
        await alice._execute_owned("UPDATE widgets SET name = ?", ("x",))
