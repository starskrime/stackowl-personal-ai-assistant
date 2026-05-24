"""DbPool — single aiosqlite connection for StackOwl's runtime lifetime."""

from __future__ import annotations

import logging
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import aiosqlite
import platformdirs

log = logging.getLogger("stackowl.db")

_PRAGMAS = [
    "PRAGMA journal_mode=WAL",
    "PRAGMA foreign_keys=ON",
    "PRAGMA busy_timeout=5000",
]


def default_db_path() -> Path:
    """Return the default database path, honouring STACKOWL_DATA_DIR if set."""
    data_dir = os.environ.get("STACKOWL_DATA_DIR")
    base = Path(data_dir) if data_dir else Path(platformdirs.user_data_dir("stackowl"))
    return base / "stackowl.db"


class DbPool:
    """Holds a single aiosqlite connection for the process lifetime.

    Public API is intentionally minimal — only ``execute`` and ``fetch_all``
    are exposed outside the ``db/`` package.
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self._path = db_path or default_db_path()
        self._conn: aiosqlite.Connection | None = None

    async def open(self) -> None:
        log.debug("[db] pool.open: entry — path=%s", self._path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        for pragma in _PRAGMAS:
            await self._conn.execute(pragma)
        await self._conn.commit()
        log.info("[db] pool.open: exit — connection established path=%s", self._path)

    async def close(self) -> None:
        log.debug("[db] pool.close: entry")
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
            log.info("[db] pool.close: exit — connection closed")

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> None:
        """Execute a write statement and commit."""
        log.debug("[db] pool.execute: entry — sql_len=%d params_count=%d", len(sql), len(params))
        if self._conn is None:
            raise RuntimeError("DbPool is not open — call open() first")
        await self._conn.execute(sql, params)
        await self._conn.commit()
        log.debug("[db] pool.execute: exit — committed")

    async def fetch_all(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        """Execute a read statement and return all rows as dicts."""
        log.debug("[db] pool.fetch_all: entry — sql_len=%d", len(sql))
        if self._conn is None:
            raise RuntimeError("DbPool is not open — call open() first")
        async with self._conn.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
            desc = cursor.description
            if not desc:
                log.debug("[db] pool.fetch_all: exit — no rows (no description)")
                return []
            keys = [d[0] for d in desc]
            result = [dict(zip(keys, tuple(row), strict=False)) for row in rows]
        log.debug("[db] pool.fetch_all: exit — row_count=%d", len(result))
        return result
