"""PrincipalStore — async SQLite wrapper for the ``principals`` table (0042).

Principals are the ROOT of the ownership model, so this Store is intentionally
NOT owner-scoped (it does not subclass :class:`OwnedRepository`): a principal
cannot be owned by a principal. It mirrors the existing Store shape — takes a
:class:`~stackowl.db.pool.DbPool`, exposes async CRUD — so it sits naturally
alongside the other domain stores.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from stackowl.db.pool import DbPool
from stackowl.infra.observability import log
from stackowl.tenancy.principal import DEFAULT_PRINCIPAL_ID, Principal

_DEFAULT_DISPLAY_NAME = "Default Owner"

_SELECT_FIELDS = "principal_id, principal_type, display_name, created_at"


class PrincipalStore:
    """CRUD over the ``principals`` table. Not owner-scoped (principals are root)."""

    def __init__(self, db: DbPool) -> None:
        self._db = db
        log.tenancy.debug("[tenancy] principal_store.init: ready")

    async def ensure_default(self) -> None:
        """Idempotently insert the single-user default principal.

        Safe to call on every startup: an ``INSERT OR IGNORE`` keyed on the
        stable :data:`DEFAULT_PRINCIPAL_ID` primary key means a second call is
        a no-op (migration 0042 already seeds the same row, so this is a
        belt-and-braces guard for fresh in-memory pools).
        """
        # 1. ENTRY
        log.tenancy.debug(
            "[tenancy] principal_store.ensure_default: entry",
            extra={"_fields": {"principal_id": DEFAULT_PRINCIPAL_ID}},
        )
        now = datetime.now(tz=UTC).isoformat()
        try:
            # 3. STEP — idempotent insert keyed on the PK
            await self._db.execute(
                "INSERT OR IGNORE INTO principals "
                "(principal_id, principal_type, display_name, created_at) "
                "VALUES (?, ?, ?, ?)",
                (DEFAULT_PRINCIPAL_ID, "user", _DEFAULT_DISPLAY_NAME, now),
            )
        except Exception as exc:
            log.tenancy.error(
                "[tenancy] principal_store.ensure_default: insert failed",
                exc_info=exc,
                extra={"_fields": {"principal_id": DEFAULT_PRINCIPAL_ID}},
            )
            raise
        # 4. EXIT
        log.tenancy.info(
            "[tenancy] principal_store.ensure_default: ensured",
            extra={"_fields": {"principal_id": DEFAULT_PRINCIPAL_ID}},
        )

    async def get(self, principal_id: str) -> Principal | None:
        """Return one principal by id, or ``None`` if it does not exist."""
        # 1. ENTRY
        log.tenancy.debug(
            "[tenancy] principal_store.get: entry",
            extra={"_fields": {"principal_id": principal_id}},
        )
        try:
            rows = await self._db.fetch_all(
                f"SELECT {_SELECT_FIELDS} FROM principals WHERE principal_id = ?",
                (principal_id,),
            )
        except Exception as exc:
            log.tenancy.error(
                "[tenancy] principal_store.get: query failed",
                exc_info=exc,
                extra={"_fields": {"principal_id": principal_id}},
            )
            raise
        # 2. DECISION + 4. EXIT
        if not rows:
            log.tenancy.debug(
                "[tenancy] principal_store.get: exit — miss",
                extra={"_fields": {"principal_id": principal_id}},
            )
            return None
        try:
            principal = _row_to_principal(rows[0])
        except Exception as exc:
            log.tenancy.error(
                "[tenancy] principal_store.get: deserialization failed",
                exc_info=exc,
                extra={"_fields": {"principal_id": principal_id}},
            )
            raise
        log.tenancy.debug(
            "[tenancy] principal_store.get: exit — hit",
            extra={"_fields": {"principal_id": principal_id}},
        )
        return principal

    async def create(self, principal: Principal) -> None:
        """Insert a new principal. Raises if the id already exists."""
        # 1. ENTRY
        log.tenancy.debug(
            "[tenancy] principal_store.create: entry",
            extra={"_fields": {
                "principal_id": principal.principal_id,
                "principal_type": principal.principal_type,
            }},
        )
        try:
            # 3. STEP — strict insert (no OR IGNORE: duplicates are an error)
            await self._db.execute(
                "INSERT INTO principals "
                "(principal_id, principal_type, display_name, created_at) "
                "VALUES (?, ?, ?, ?)",
                (
                    principal.principal_id,
                    principal.principal_type,
                    principal.display_name,
                    principal.created_at.isoformat(),
                ),
            )
        except Exception as exc:
            log.tenancy.error(
                "[tenancy] principal_store.create: insert failed",
                exc_info=exc,
                extra={"_fields": {"principal_id": principal.principal_id}},
            )
            raise
        # 4. EXIT
        log.tenancy.info(
            "[tenancy] principal_store.create: created",
            extra={"_fields": {"principal_id": principal.principal_id}},
        )

    async def list(self) -> list[Principal]:
        """Return every principal, ordered by ``created_at`` then id."""
        # 1. ENTRY
        log.tenancy.debug("[tenancy] principal_store.list: entry")
        try:
            rows = await self._db.fetch_all(
                f"SELECT {_SELECT_FIELDS} FROM principals "
                "ORDER BY created_at, principal_id",
            )
        except Exception as exc:
            log.tenancy.error(
                "[tenancy] principal_store.list: query failed",
                exc_info=exc,
            )
            raise
        try:
            results = [_row_to_principal(r) for r in rows]
        except Exception as exc:
            log.tenancy.error(
                "[tenancy] principal_store.list: deserialization failed",
                exc_info=exc,
            )
            raise
        # 4. EXIT
        log.tenancy.debug(
            "[tenancy] principal_store.list: exit",
            extra={"_fields": {"count": len(results)}},
        )
        return results


def _row_to_principal(row: dict[str, Any]) -> Principal:
    """Map one ``principals`` row dict to a :class:`Principal`."""
    return Principal(
        principal_id=str(row["principal_id"]),
        principal_type=str(row["principal_type"]),  # type: ignore[arg-type]
        display_name=str(row["display_name"]),
        created_at=datetime.fromisoformat(str(row["created_at"])),
    )
