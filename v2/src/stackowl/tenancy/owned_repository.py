"""OwnedRepository — owner-scoped base class for domain Store classes (Pass 1).

This is the tenancy foundation: a thin, ABC-friendly base that wraps the
existing :class:`~stackowl.db.pool.DbPool` and binds every read/write to a
single ``owner_id`` (a principal id). Domain Stores subclass it (in a later
pass) so their queries are auto-scoped by owner without each Store hand-rolling
``WHERE owner_id = ?`` — cross-owner reads/writes become structurally
impossible.

It deliberately mirrors the existing Store shape (constructor takes a
``DbPool``; protected async helpers do the SQL) so subclassing is a drop-in
extension, not a rewrite. The concrete table name is supplied by the subclass
via :attr:`_table` — this class never hardcodes a table.

Pass-1 scope: NO existing Store is migrated onto this base here. This class
only provides the reusable mechanism and its own unit tests.
"""

from __future__ import annotations

import re
from abc import ABC
from collections.abc import Sequence
from typing import Any

from stackowl.db.pool import DbPool
from stackowl.infra.observability import log

#: Permit only safe SQL identifier characters (letters, digits, underscore).
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _is_paren_balanced(fragment: str) -> bool:
    """True iff round-parentheses in ``fragment`` are balanced and never go negative.

    The running depth must never drop below zero (which would mean a ``)`` closes a
    paren the fragment never opened — e.g. ``1=1) OR (1=1`` could close the base's
    own ``(`` early) and must end at exactly zero (no dangling open). A pure count
    is insufficient: ``1=1) OR (1=1`` has one ``(`` and one ``)`` yet escapes. This
    is a structural latch, not a SQL parser (the fragment is developer-authored).
    """
    depth = 0
    for ch in fragment:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


class OwnedRepository(ABC):  # noqa: B024 — abstract by contract: subclasses must set `_table`
    """Owner-scoped repository base. Subclasses set :attr:`_table`.

    All helpers automatically constrain to ``self._owner_id`` so a subclass can
    never accidentally read or write another principal's rows.
    """

    #: Subclasses MUST override with the backing table name.
    _table: str = ""

    def __init__(self, db: DbPool, owner_id: str) -> None:
        # 1. ENTRY
        log.tenancy.debug(
            "[tenancy] owned_repo.init: entry",
            extra={"_fields": {"cls": type(self).__name__, "owner_id": owner_id}},
        )
        if not self._table:
            # Fail loud: a subclass that forgot to declare its table is a bug
            # that would otherwise silently build malformed SQL.
            msg = f"{type(self).__name__} must set a non-empty '_table'"
            log.tenancy.error(
                "[tenancy] owned_repo.init: missing _table",
                extra={"_fields": {"cls": type(self).__name__}},
            )
            raise ValueError(msg)
        if not owner_id:
            msg = "owner_id must be a non-empty principal id"
            log.tenancy.error(
                "[tenancy] owned_repo.init: empty owner_id",
                extra={"_fields": {"cls": type(self).__name__}},
            )
            raise ValueError(msg)
        self._db = db
        self._owner_id = owner_id
        # 4. EXIT
        log.tenancy.debug(
            "[tenancy] owned_repo.init: exit",
            extra={"_fields": {"cls": type(self).__name__, "table": self._table}},
        )

    @property
    def owner_id(self) -> str:
        """The principal id this repository is bound to."""
        return self._owner_id

    async def _fetch_owned(
        self,
        table: str,
        where_sql: str = "",
        params: Sequence[Any] = (),
    ) -> list[dict[str, Any]]:
        """``SELECT * FROM {table} WHERE owner_id = ? [AND {where_sql}]``.

        ``owner_id`` is always the FIRST bound parameter; ``params`` fill the
        placeholders in ``where_sql`` (which must NOT include the owner clause).
        """
        # 1. ENTRY
        log.tenancy.debug(
            "[tenancy] owned_repo.fetch_owned: entry",
            extra={"_fields": {
                "table": table, "owner_id": self._owner_id,
                "has_where": bool(where_sql),
            }},
        )
        # 2. DECISION — compose the scoped predicate
        extra = where_sql.strip()
        clause = "WHERE owner_id = ?"
        if extra:
            clause += f" AND ({extra})"
        sql = f"SELECT * FROM {table} {clause}"  # noqa: S608 — table from subclass, not user input
        bound: tuple[Any, ...] = (self._owner_id, *tuple(params))
        try:
            # 3. STEP — execute the scoped read
            rows = await self._db.fetch_all(sql, bound)
        except Exception as exc:
            log.tenancy.error(
                "[tenancy] owned_repo.fetch_owned: query failed",
                exc_info=exc,
                extra={"_fields": {"table": table, "owner_id": self._owner_id}},
            )
            raise
        # 4. EXIT
        log.tenancy.debug(
            "[tenancy] owned_repo.fetch_owned: exit",
            extra={"_fields": {"table": table, "row_count": len(rows)}},
        )
        return rows

    async def _update_owned(
        self,
        table: str,
        *,
        set_sql: str,
        set_params: Sequence[Any] = (),
        where_sql: str = "",
        where_params: Sequence[Any] = (),
    ) -> None:
        """Owner-scoped ``UPDATE {table} SET {set_sql} WHERE owner_id = ? [AND (...)]``.

        STRUCTURAL owner scoping (F136): the owner predicate is composed HERE —
        the base appends ``owner_id = ?`` bound to ``self._owner_id`` and any
        caller-supplied ``where_sql`` is conjoined inside parentheses. A subclass
        therefore cannot escape owner scope with a crafted predicate (e.g.
        ``OR 1=1``): the owner clause is always AND-ed OUTSIDE the caller's
        parenthesized extra, so it can only ever NARROW the affected rows.

        GUARANTEE: ``where_sql`` can ONLY narrow, never widen. To keep that true
        the fragment MUST be parenthesis-balanced — an unbalanced fragment (e.g.
        ``1=1) OR (1=1``) would close the wrapping paren early and AND-attach an
        always-true predicate outside owner scope, so it is REJECTED with a
        ``ValueError`` before any query is composed.
        """
        await self._owner_scoped_write(
            "UPDATE", table, set_sql=set_sql, set_params=set_params,
            where_sql=where_sql, where_params=where_params,
        )

    async def _delete_owned(
        self,
        table: str,
        *,
        where_sql: str = "",
        where_params: Sequence[Any] = (),
    ) -> None:
        """Owner-scoped ``DELETE FROM {table} WHERE owner_id = ? [AND (...)]``.

        Same structural guarantee as :meth:`_update_owned`: the owner predicate is
        composed by the base and bound to ``self._owner_id``; a caller predicate
        can only narrow, never widen, the affected rows. The ``where_sql`` fragment
        MUST be parenthesis-balanced (an unbalanced fragment that could escape the
        wrapping parens is rejected with a ``ValueError`` before composing).
        """
        await self._owner_scoped_write(
            "DELETE", table, where_sql=where_sql, where_params=where_params,
        )

    async def _owner_scoped_write(
        self,
        verb: str,
        table: str,
        *,
        set_sql: str = "",
        set_params: Sequence[Any] = (),
        where_sql: str = "",
        where_params: Sequence[Any] = (),
    ) -> None:
        """Compose + run a structurally owner-scoped UPDATE/DELETE (shared core)."""
        # 1. ENTRY
        log.tenancy.debug(
            "[tenancy] owned_repo.owner_scoped_write: entry",
            extra={"_fields": {
                "verb": verb, "table": table, "owner_id": self._owner_id,
                "has_where": bool(where_sql),
            }},
        )
        # 2. DECISION — validate the structural inputs (fail closed on anything odd).
        if verb not in ("UPDATE", "DELETE"):
            raise ValueError(f"unsupported verb {verb!r} (only UPDATE/DELETE)")
        if not _IDENT_RE.match(table):
            raise ValueError(f"unsafe table name: {table!r}")
        params: list[Any] = []
        if verb == "UPDATE":
            if not set_sql.strip():
                raise ValueError("UPDATE requires a non-empty set_sql")
            head = f"UPDATE {table} SET {set_sql.strip()}"  # noqa: S608 — table validated, columns literal
            params.extend(set_params)
        else:
            head = f"DELETE FROM {table}"  # noqa: S608 — table validated
        # The owner clause is composed HERE and bound to self._owner_id — NOT
        # supplied by the caller. The caller's predicate is conjoined INSIDE
        # parentheses so it can only narrow (an OR inside the parens cannot reach
        # past the structural ``owner_id = ?`` AND-ed outside it).
        clause = "WHERE owner_id = ?"
        params.append(self._owner_id)
        extra = where_sql.strip()
        if extra:
            # The fragment is conjoined INSIDE parentheses. An UNBALANCED fragment
            # (e.g. ``1=1) OR (1=1``) would textually close the base's open paren
            # early and AND-attach an always-true predicate OUTSIDE owner scope —
            # making the "can ONLY narrow" guarantee a lie. Refuse it BEFORE
            # composing (a balanced-paren check is sufficient; the fragment is
            # developer-authored, not user input, so this is a defense-in-depth
            # latch, not a SQL parser).
            if not _is_paren_balanced(extra):
                raise ValueError(
                    "where_sql fragment must be parenthesis-balanced "
                    "(a fragment that opens/closes more parens than it matches "
                    "could escape the structural owner_id scope) — refusing"
                )
            clause += f" AND ({extra})"
            params.extend(where_params)
        sql = f"{head} {clause}"
        try:
            # 3. STEP — owner-scoped write through the self-healing pool.
            await self._db.execute(sql, params)
        except Exception as exc:
            log.tenancy.error(
                "[tenancy] owned_repo.owner_scoped_write: write failed",
                exc_info=exc,
                extra={"_fields": {"verb": verb, "table": table, "owner_id": self._owner_id}},
            )
            raise
        # 4. EXIT
        log.tenancy.debug(
            "[tenancy] owned_repo.owner_scoped_write: exit",
            extra={"_fields": {"verb": verb, "table": table, "owner_id": self._owner_id}},
        )

    async def _execute_owned(self, sql: str, params: Sequence[Any]) -> None:
        """Legacy raw-SQL escape hatch for an already owner-scoped UPDATE/DELETE.

        Prefer :meth:`_update_owned` / :meth:`_delete_owned` (the owner predicate
        is composed structurally there). This hatch remains for callers that build
        a complex dynamic SET clause, but its guard is now STRUCTURAL, not a
        substring test (F136): the SQL MUST contain the canonical bound predicate
        ``owner_id = ?`` (whitespace-insensitive) AND ``self._owner_id`` MUST be
        among ``params``. This refuses the escapes the old substring guard let
        through — ``owner_id IS NOT NULL`` (no ``= ?``) and binding another
        principal's id.
        """
        # 1. ENTRY
        log.tenancy.debug(
            "[tenancy] owned_repo.execute_owned: entry",
            extra={"_fields": {
                "owner_id": self._owner_id, "sql_len": len(sql),
                "params_count": len(params),
            }},
        )
        # 2. DECISION — fail-closed: SQL must bind owner_id with the canonical
        # ``owner_id = ?`` form AND self._owner_id must be one of the bound params.
        normalized = " ".join(sql.lower().split())
        if "owner_id = ?" not in normalized and "owner_id=?" not in normalized:
            log.tenancy.error(
                "[tenancy] owned_repo.execute_owned: no canonical 'owner_id = ?' predicate — refusing write",
                extra={"_fields": {"owner_id": self._owner_id, "sql_len": len(sql)}},
            )
            raise ValueError(
                "_execute_owned requires a canonical bound 'owner_id = ?' predicate"
                " — refusing potential cross-owner write"
            )
        if self._owner_id not in tuple(params):
            log.tenancy.error(
                "[tenancy] owned_repo.execute_owned: bound owner not in params — refusing write",
                extra={"_fields": {"owner_id": self._owner_id}},
            )
            raise ValueError(
                "_execute_owned requires self._owner_id to be bound in params"
                " — refusing potential cross-owner write"
            )
        try:
            # 3. STEP — delegate to the self-healing pool
            await self._db.execute(sql, params)
        except Exception as exc:
            log.tenancy.error(
                "[tenancy] owned_repo.execute_owned: write failed",
                exc_info=exc,
                extra={"_fields": {"owner_id": self._owner_id}},
            )
            raise
        # 4. EXIT
        log.tenancy.debug(
            "[tenancy] owned_repo.execute_owned: exit",
            extra={"_fields": {"owner_id": self._owner_id}},
        )

    async def _insert_owned(self, table: str, columns: dict[str, Any]) -> None:
        """``INSERT INTO {table} (...)`` with ``owner_id`` injected automatically.

        The caller passes only domain columns; this method stamps the bound
        ``owner_id`` so a subclass can never forget it. If the caller *does*
        pass ``owner_id`` it must match the bound owner — a mismatch is a
        programming error and fails loud (no silent cross-owner write).
        """
        # 1. ENTRY
        log.tenancy.debug(
            "[tenancy] owned_repo.insert_owned: entry",
            extra={"_fields": {
                "table": table, "owner_id": self._owner_id,
                "columns": sorted(columns.keys()),
            }},
        )
        # 2. DECISION — validate column names and reconcile any caller-supplied owner_id
        for col in columns:
            if not _IDENT_RE.match(col):
                raise ValueError(f"Unsafe column name in _insert_owned: {col!r}")
        supplied = columns.get("owner_id")
        if supplied is not None and supplied != self._owner_id:
            msg = (
                f"owner_id mismatch on insert into {table}: "
                f"bound={self._owner_id!r} supplied={supplied!r}"
            )
            log.tenancy.error(
                "[tenancy] owned_repo.insert_owned: owner mismatch",
                extra={"_fields": {
                    "table": table, "bound": self._owner_id, "supplied": supplied,
                }},
            )
            raise ValueError(msg)
        scoped: dict[str, Any] = {**columns, "owner_id": self._owner_id}
        cols = list(scoped.keys())
        placeholders = ", ".join("?" for _ in cols)
        col_sql = ", ".join(cols)
        sql = f"INSERT INTO {table} ({col_sql}) VALUES ({placeholders})"  # noqa: S608 — names from subclass
        values = tuple(scoped[c] for c in cols)
        try:
            # 3. STEP — execute the stamped insert
            await self._db.execute(sql, values)
        except Exception as exc:
            log.tenancy.error(
                "[tenancy] owned_repo.insert_owned: insert failed",
                exc_info=exc,
                extra={"_fields": {"table": table, "owner_id": self._owner_id}},
            )
            raise
        # 4. EXIT
        log.tenancy.debug(
            "[tenancy] owned_repo.insert_owned: exit",
            extra={"_fields": {"table": table, "owner_id": self._owner_id}},
        )
