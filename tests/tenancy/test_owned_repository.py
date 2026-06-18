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
        """Owner-scoped UPDATE — owner predicate is composed STRUCTURALLY by the base.

        The caller supplies only the table, the SET clause + params, and an
        optional extra predicate; ``_update_owned`` appends ``owner_id = ?``
        bound to ``self._owner_id`` itself (a sloppy subclass cannot escape scope).
        """
        await self._update_owned(
            "widgets",
            set_sql="name = ?",
            set_params=(new_name,),
            where_sql="name = ?",
            where_params=(old_name,),
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


async def test_update_owned_rejects_unsafe_table(pool: DbPool) -> None:
    """The table name must be a safe SQL identifier (no injection via table)."""
    alice = _WidgetRepo(pool, "principal-alice")
    with pytest.raises(ValueError, match="table"):
        await alice._update_owned(
            "widgets; DROP TABLE widgets", set_sql="name = ?", set_params=("x",)
        )


async def test_update_owned_owner_predicate_is_structural_not_substring(
    pool: DbPool,
) -> None:
    """A caller cannot escape owner scope via a crafted extra predicate.

    The owner clause is composed by the base (``owner_id = ?`` bound to
    ``self._owner_id``) and the caller's extra predicate is AND-ed inside
    parentheses. An attacker-style ``OR 1=1`` in the caller predicate therefore
    cannot touch another owner's rows: the structural owner clause is always
    conjoined OUTSIDE the parenthesized caller predicate. The OLD substring guard
    would have happily accepted ``WHERE owner_id = ? OR 1=1`` hand-written by a
    sloppy subclass — the new structural composition makes that impossible.
    """
    alice = _WidgetRepo(pool, "principal-alice")
    bob = _WidgetRepo(pool, "principal-bob")
    await alice.add("a1")
    await bob.add("b1")

    await alice._delete_owned("widgets", where_sql="name = ? OR 1=1", where_params=("a1",))

    assert await alice.names() == []  # alice's rows gone
    assert await bob.names() == ["b1"]  # bob's row UNTOUCHED — owner scope held


async def test_delete_owned_is_owner_scoped(pool: DbPool) -> None:
    """A no-extra-predicate DELETE removes only the caller's rows."""
    alice = _WidgetRepo(pool, "principal-alice")
    bob = _WidgetRepo(pool, "principal-bob")
    await alice.add("a1")
    await alice.add("a2")
    await bob.add("b1")

    await alice._delete_owned("widgets")

    assert await alice.names() == []
    assert await bob.names() == ["b1"]


async def test_execute_owned_legacy_rejects_uncanonical_owner_predicate(
    pool: DbPool,
) -> None:
    """The legacy raw-SQL escape hatch demands a CANONICAL bound owner predicate.

    ``WHERE owner_id IS NOT NULL`` (the substring-passing escape the old guard
    allowed) is now refused: the guard requires literal ``owner_id = ?`` AND the
    bound owner among the params.
    """
    alice = _WidgetRepo(pool, "principal-alice")
    with pytest.raises(ValueError, match="owner_id"):
        await alice._execute_owned(
            "DELETE FROM widgets WHERE owner_id IS NOT NULL", ()
        )


async def test_execute_owned_legacy_rejects_owner_not_in_params(pool: DbPool) -> None:
    """Even with ``owner_id = ?`` present, the bound owner MUST be a param."""
    alice = _WidgetRepo(pool, "principal-alice")
    with pytest.raises(ValueError, match="owner"):
        await alice._execute_owned(
            "DELETE FROM widgets WHERE owner_id = ?", ("principal-bob",)
        )


# --- SEC-1 hardening: balanced-paren guard on the caller where_sql fragment ------


async def test_delete_owned_rejects_unbalanced_where_sql_paren_escape(
    pool: DbPool,
) -> None:
    """A developer-authored UNBALANCED fragment cannot break out of the parens.

    The base conjoins ``owner_id = ? AND (<where_sql>)``. A fragment like
    ``1=1) OR (1=1`` would (textually) close the base's open paren early and
    AND-attach an always-true ``OR (1=1...)`` OUTSIDE owner scope. The composer
    now validates the fragment is paren-balanced BEFORE composing and refuses an
    unbalanced one rather than emit an owner-scope-escaping query.
    """
    alice = _WidgetRepo(pool, "principal-alice")
    bob = _WidgetRepo(pool, "principal-bob")
    await alice.add("a1")
    await bob.add("b1")

    with pytest.raises(ValueError, match="balanced|parenthes"):
        await alice._delete_owned("widgets", where_sql="1=1) OR (1=1")

    # The refusal means NO query ran — bob's row is untouched, owner scope held.
    assert await bob.names() == ["b1"]


async def test_update_owned_rejects_unbalanced_where_sql_paren_escape(
    pool: DbPool,
) -> None:
    """``_update_owned`` shares the same balanced-fragment guard as delete."""
    alice = _WidgetRepo(pool, "principal-alice")
    bob = _WidgetRepo(pool, "principal-bob")
    await alice.add("a1")
    await bob.add("b1")

    with pytest.raises(ValueError, match="balanced|parenthes"):
        await alice._update_owned(
            "widgets",
            set_sql="name = ?",
            set_params=("hacked",),
            where_sql="1=1) OR (1=1",
        )

    # No query ran — nobody's name was changed.
    assert await alice.names() == ["a1"]
    assert await bob.names() == ["b1"]


async def test_update_owned_rejects_trailing_unbalanced_close_paren(
    pool: DbPool,
) -> None:
    """A lone trailing ``)`` (or leading ``(``) is unbalanced and refused."""
    alice = _WidgetRepo(pool, "principal-alice")
    with pytest.raises(ValueError, match="balanced|parenthes"):
        await alice._update_owned(
            "widgets", set_sql="name = ?", set_params=("x",), where_sql="name = ?)"
        )


async def test_balanced_where_sql_fragment_still_narrows(pool: DbPool) -> None:
    """A normal BALANCED fragment is accepted and only narrows the affected rows."""
    alice = _WidgetRepo(pool, "principal-alice")
    bob = _WidgetRepo(pool, "principal-bob")
    await alice.add("a1")
    await alice.add("a2")
    await bob.add("b1")

    # Balanced fragment (even one with its own matched parens) is fine.
    await alice._delete_owned(
        "widgets", where_sql="(name = ?)", where_params=("a1",)
    )

    assert await alice.names() == ["a2"]  # only the matched row removed
    assert await bob.names() == ["b1"]  # other owner untouched
