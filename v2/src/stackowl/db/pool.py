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
import sqlite3
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
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

# Primary SQLite error codes that indicate a transient connection-level failure
# the pool can recover from by reconnecting (F022). These are matched on the
# exception's ``sqlite_errorcode`` (Python 3.11+) MASKED to its primary code, so
# extended variants (e.g. SQLITE_IOERR_*) collapse to their base. Crucially
# SQLITE_ERROR (the code for "no such table" and other logic errors) is NOT
# here, so a missing table surfaces loudly instead of triggering a reconnect
# loop.
_DEAD_PRIMARY_CODES: frozenset[int] = frozenset(
    {
        sqlite3.SQLITE_BUSY,
        sqlite3.SQLITE_IOERR,
        sqlite3.SQLITE_CANTOPEN,
        sqlite3.SQLITE_NOTADB,
        sqlite3.SQLITE_CORRUPT,
        sqlite3.SQLITE_PROTOCOL,
    }
)


def _looks_like_dead_sqlite(exc: BaseException) -> bool:
    """True iff ``exc`` is a recoverable SQLite connection-death failure.

    Keys on the sqlite3 exception TYPE + ``sqlite_errorcode`` rather than the
    English error text (F022). A closed/disposed connection surfaces as a
    ``sqlite3.ProgrammingError`` (aiosqlite raises this after ``.close()``) and
    is always treated as dead. ``OperationalError`` is dead only for the
    transient primary codes in :data:`_DEAD_PRIMARY_CODES`; ``SQLITE_ERROR``
    (missing table / bad column) is a logic error and is NOT recovered.
    """
    # A closed connection (or other invalid-handle misuse) is always recoverable
    # by reconnecting — independent of any error text.
    if isinstance(exc, sqlite3.ProgrammingError):
        return True
    # aiosqlite raises a bare ``ValueError("no active connection")`` from its
    # worker thread when the connection was closed/disposed — a genuine dead
    # handle that carries NO sqlite errorcode (it never reached SQLite). This is
    # a library-internal sentinel, not a SQLite logic error, so matching it is
    # safe and does not regress the "missing table surfaces loudly" guarantee.
    if isinstance(exc, ValueError) and "no active connection" in str(exc):
        return True
    if isinstance(exc, sqlite3.OperationalError | sqlite3.DatabaseError):
        code = getattr(exc, "sqlite_errorcode", None)
        if code is None:
            # No errorcode available (shouldn't happen on 3.11+, but never
            # silently swallow): treat as NOT dead so a logic error surfaces.
            log.warning(
                "[db] _looks_like_dead_sqlite: sqlite exception without errorcode "
                "— treating as logic error (no reconnect): %r",
                exc,
            )
            return False
        primary = int(code) & 0xFF  # mask extended code to its primary code
        return primary in _DEAD_PRIMARY_CODES
    return False


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
        # Serializes WRITE operations (execute / execute_returning_rowcount /
        # transaction) against each other on the single shared connection (F070):
        # a multi-statement transaction()'s BEGIN IMMEDIATE must not be committed
        # out from under it by a concurrent execute()'s implicit commit. Distinct
        # from ``_lock`` (the connection-open lock) so retry's ensure_available()
        # never self-deadlocks. Reads (fetch_all) intentionally do NOT take it —
        # they never commit and tolerate seeing pre-txn data.
        self._write_lock = asyncio.Lock()
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
                if _looks_like_dead_sqlite(exc):
                    self._mark_dead(f"execute failed: {type(exc).__name__}: {exc}")
                raise

        async with self._write_lock:
            await retry_once_on_dead_handle(
                _do, self, op_name="db.execute",
                is_dead=_looks_like_dead_sqlite,
            )
        log.debug("[db] pool.execute: exit — committed")

    async def execute_returning_rowcount(
        self, sql: str, params: Sequence[Any] = ()
    ) -> int:
        """Execute a write statement, commit, and return rows-affected.

        Unlike :meth:`execute` (which discards the cursor), this surfaces the
        cursor's ``rowcount`` so callers can build a compare-and-swap claim — an
        ``UPDATE ... WHERE status='running'`` that reports whether IT won the
        race (1 row) or another worker already claimed the row (0 rows). Used by
        B4 crash-recovery to atomically latch one orphaned task at a time.
        Self-heals on connection death exactly like :meth:`execute`.
        """
        # 1. ENTRY
        log.debug(
            "[db] pool.execute_returning_rowcount: entry — sql_len=%d params_count=%d",
            len(sql), len(params),
        )
        await self.ensure_available()

        async def _do() -> int:
            if self._conn is None:
                raise RuntimeError("DbPool: connection unexpectedly None")
            try:
                cursor = await self._conn.execute(sql, params)
                # 3. STEP — capture rowcount BEFORE commit (sqlite keeps it valid).
                affected = cursor.rowcount
                await self._conn.commit()
                return int(affected)
            except Exception as exc:
                if _looks_like_dead_sqlite(exc):
                    self._mark_dead(
                        f"execute_returning_rowcount failed: {type(exc).__name__}: {exc}"
                    )
                raise

        async with self._write_lock:
            result = await retry_once_on_dead_handle(
                _do, self, op_name="db.execute_returning_rowcount",
                is_dead=_looks_like_dead_sqlite,
            )
        # 4. EXIT
        log.debug(
            "[db] pool.execute_returning_rowcount: exit — rows_affected=%d", result
        )
        return result

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        """Run several statements atomically in ONE committed transaction (F070).

        Yields the live connection inside a ``BEGIN IMMEDIATE`` write txn; the
        body issues ``await conn.execute(sql, params)`` calls (no per-statement
        commit). On clean exit the txn COMMITs; on ANY exception it ROLLBACKs and
        re-raises so the base table and a derived index (e.g. ``committed_facts``
        + ``committed_facts_fts``) can never diverge on a mid-sequence failure.

        Held under the pool's connection lock for the txn's duration so it cannot
        interleave with another ``execute``/``transaction`` on the single shared
        connection (which would corrupt the in-flight write txn). The lock is
        released the moment the txn ends. NOT self-healed mid-flight: a dead
        handle inside a txn aborts it (rollback semantics demand the caller retry
        the whole unit), but the dead handle is marked so the NEXT call reopens.
        """
        # 1. ENTRY
        log.debug("[db] pool.transaction: entry")
        await self.ensure_available()
        async with self._write_lock:
            if self._conn is None:
                raise RuntimeError("DbPool: connection unexpectedly None")
            conn = self._conn
            try:
                await conn.execute("BEGIN IMMEDIATE")
            except Exception as exc:
                if _looks_like_dead_sqlite(exc):
                    self._mark_dead(f"transaction begin failed: {type(exc).__name__}: {exc}")
                log.error("[db] pool.transaction: BEGIN failed", exc_info=exc)
                raise
            try:
                yield conn
            except Exception as exc:
                # 3. STEP — roll back the whole unit; never leave a half-applied txn.
                try:
                    await conn.rollback()
                except Exception as rb_exc:  # never mask the original; log loudly
                    if _looks_like_dead_sqlite(rb_exc):
                        self._mark_dead(
                            f"transaction rollback failed: {type(rb_exc).__name__}: {rb_exc}"
                        )
                    log.error("[db] pool.transaction: ROLLBACK failed", exc_info=rb_exc)
                if _looks_like_dead_sqlite(exc):
                    self._mark_dead(f"transaction body failed: {type(exc).__name__}: {exc}")
                log.warning("[db] pool.transaction: rolled back on error", exc_info=exc)
                raise
            else:
                await conn.commit()
        # 4. EXIT
        log.debug("[db] pool.transaction: exit — committed")

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
                if _looks_like_dead_sqlite(exc):
                    self._mark_dead(f"fetch_all failed: {type(exc).__name__}: {exc}")
                raise

        result = await retry_once_on_dead_handle(
            _do, self, op_name="db.fetch_all",
            is_dead=_looks_like_dead_sqlite,
        )
        log.debug("[db] pool.fetch_all: exit — row_count=%d", len(result))
        return result
