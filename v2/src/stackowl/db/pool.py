"""DbPool — single aiosqlite connection for StackOwl's runtime lifetime.

Implements the :class:`HealableResource` protocol: detects SQLite
connection-death errors (locked DB, disk I/O error, broken handle) and
reconnects on demand. ``execute`` / ``fetch_all`` retry exactly once via
:func:`retry_once_on_dead_handle` so transient SQLite failures never bubble
up to the caller.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import aiosqlite

from stackowl.infra.resilience import retry_once_on_dead_handle
from stackowl.paths import StackowlHome

log = logging.getLogger("stackowl.db")

_PRAGMAS = [
    "PRAGMA journal_mode=WAL",
    "PRAGMA foreign_keys=ON",
    "PRAGMA busy_timeout=5000",
]

# SQLite-specific error markers used in addition to the default dead-handle list.
_SQLITE_DEAD_MARKERS = (
    "database is locked",
    "disk I/O error",
    "unable to open database",
    "no such table",
    "Cannot operate on a closed database",
    "Connection closed",
    "no active connection",  # aiosqlite raises this after .close()
    "Cannot operate on a closed",
)


def default_db_path() -> Path:
    """Return the default database path."""
    return StackowlHome.db_path()


class DbPool:
    """Holds a single aiosqlite connection for the process lifetime.

    Public API is intentionally minimal — only ``execute`` and ``fetch_all``
    are exposed outside the ``db/`` package. Both self-heal on connection
    death (one retry per call) via :class:`HealableResource` integration.
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self._path = db_path or default_db_path()
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()
        self._unavailable_reason: str | None = None
        self._on_recycled_cbs: list[Callable[[], None]] = []
        self._recycle_count: int = 0
        self._last_recycle_at: float | None = None
        self._last_recycle_reason: str | None = None

    # ---- HealableResource protocol ----------------------------------------

    @property
    def available(self) -> bool:
        return self._conn is not None

    @property
    def unavailable_reason(self) -> str | None:
        return self._unavailable_reason

    @property
    def recycle_count(self) -> int:
        return self._recycle_count

    @property
    def last_recycle_at(self) -> float | None:
        return self._last_recycle_at

    @property
    def last_recycle_reason(self) -> str | None:
        return self._last_recycle_reason

    def register_on_recycled(self, cb: Callable[[], None]) -> None:
        self._on_recycled_cbs.append(cb)

    def _fire_on_recycled(self) -> None:
        for cb in self._on_recycled_cbs:
            try:
                cb()
            except Exception as exc:
                log.error("[db] pool.on_recycled: callback failed", exc_info=exc)

    async def ensure_available(self) -> None:
        """Ensure the pool has a live connection; reconnect if dead."""
        if self._conn is not None:
            return
        log.info(
            "[db] pool.ensure_available: reconnecting — reason=%s",
            self._unavailable_reason,
        )
        async with self._lock:
            if self._conn is not None:
                return  # raced
            await self._open_inside_lock(is_recycle=True)
        if self._conn is None:
            raise RuntimeError(
                f"db pool unavailable: {self._unavailable_reason or 'unknown'}"
            )

    # ---- lifecycle --------------------------------------------------------

    async def open(self) -> None:
        log.debug("[db] pool.open: entry — path=%s", self._path)
        async with self._lock:
            if self._conn is not None:
                log.debug("[db] pool.open: already open — no-op")
                return
            await self._open_inside_lock(is_recycle=False)
        log.info("[db] pool.open: exit — connection established path=%s", self._path)

    async def _open_inside_lock(self, *, is_recycle: bool) -> None:
        import time as _time

        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._conn = await aiosqlite.connect(self._path)
            self._conn.row_factory = aiosqlite.Row
            for pragma in _PRAGMAS:
                await self._conn.execute(pragma)
            await self._conn.commit()
            self._unavailable_reason = None
        except Exception as exc:
            self._unavailable_reason = f"{type(exc).__name__}: {exc}"
            self._conn = None
            log.error("[db] pool._open_inside_lock: connect failed", exc_info=exc)
            raise
        if is_recycle:
            self._recycle_count += 1
            self._last_recycle_at = _time.time()
            self._last_recycle_reason = self._unavailable_reason or "reconnect"
            self._fire_on_recycled()

    async def close(self) -> None:
        log.debug("[db] pool.close: entry")
        if self._conn is not None:
            try:
                await self._conn.close()
            except Exception as exc:
                log.warning("[db] pool.close: close raised — ignoring", exc_info=exc)
            self._conn = None
            log.info("[db] pool.close: exit — connection closed")

    def _mark_dead(self, reason: str) -> None:
        """Mark the connection as dead so the next ensure_available reopens it.

        Callbacks are NOT fired here — they fire after a successful reconnect
        in ``_open_inside_lock(is_recycle=True)``. This avoids double-firing
        when ``execute()`` retries via ``retry_once_on_dead_handle`` which
        always calls ``ensure_available()`` right after a dead-handle error.
        """
        log.warning("[db] pool.mark_dead: %s", reason)
        self._conn = None
        self._unavailable_reason = reason

    # ---- public IO --------------------------------------------------------

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> None:
        """Execute a write statement and commit. Self-heals on connection death."""
        log.debug(
            "[db] pool.execute: entry — sql_len=%d params_count=%d",
            len(sql), len(params),
        )
        await self.ensure_available()

        async def _do() -> None:
            if self._conn is None:
                raise RuntimeError("DbPool: connection unexpectedly None")
            try:
                await self._conn.execute(sql, params)
                await self._conn.commit()
            except Exception as exc:
                if self._looks_dead(exc):
                    self._mark_dead(f"execute failed: {type(exc).__name__}: {exc}")
                raise

        await retry_once_on_dead_handle(
            _do, self, op_name="db.execute",
            dead_markers=_SQLITE_DEAD_MARKERS,
        )
        log.debug("[db] pool.execute: exit — committed")

    async def fetch_all(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        """Execute a read statement and return all rows as dicts. Self-heals."""
        log.debug("[db] pool.fetch_all: entry — sql_len=%d", len(sql))
        await self.ensure_available()

        async def _do() -> list[dict[str, Any]]:
            if self._conn is None:
                raise RuntimeError("DbPool: connection unexpectedly None")
            try:
                async with self._conn.execute(sql, params) as cursor:
                    rows = await cursor.fetchall()
                    desc = cursor.description
                    if not desc:
                        return []
                    keys = [d[0] for d in desc]
                    return [dict(zip(keys, tuple(row), strict=False)) for row in rows]
            except Exception as exc:
                if self._looks_dead(exc):
                    self._mark_dead(f"fetch_all failed: {type(exc).__name__}: {exc}")
                raise

        result = await retry_once_on_dead_handle(
            _do, self, op_name="db.fetch_all",
            dead_markers=_SQLITE_DEAD_MARKERS,
        )
        log.debug("[db] pool.fetch_all: exit — row_count=%d", len(result))
        return result

    @staticmethod
    def _looks_dead(exc: BaseException) -> bool:
        msg = str(exc)
        return any(m in msg for m in _SQLITE_DEAD_MARKERS)
