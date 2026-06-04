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

    async def _execute_owned(self, sql: str, params: Sequence[Any]) -> None:
        """Run a write whose SQL the caller has ALREADY owner-scoped.

        This is the escape hatch for UPDATE/DELETE that need owner_id in their
        own WHERE clause. The caller is responsible for including the owner
        predicate; prefer :meth:`_insert_owned` for inserts so the stamping is
        automatic.
        """
        # 1. ENTRY
        log.tenancy.debug(
            "[tenancy] owned_repo.execute_owned: entry",
            extra={"_fields": {
                "owner_id": self._owner_id, "sql_len": len(sql),
                "params_count": len(params),
            }},
        )
        # 2. DECISION — fail-closed: SQL must contain an owner_id predicate
        if "owner_id" not in sql.lower():
            log.tenancy.error(
                "[tenancy] owned_repo.execute_owned: missing owner_id predicate — refusing write",
                extra={"_fields": {"owner_id": self._owner_id, "sql_len": len(sql)}},
            )
            raise ValueError(
                "_execute_owned requires an owner_id predicate in the SQL"
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
