"""DUR-2 / F022 — DbPool dead-handle detection keys on sqlite3 exception TYPE +
sqlite_errorcode, never English substring matching.

A genuinely missing table (``OperationalError`` with ``SQLITE_ERROR``) must
surface loudly as a logic error — NOT be retried as a dead handle (no reconnect
loop). A real connection-death class (BUSY / IOERR / CANTOPEN, or a
closed-connection ``ProgrammingError``) still self-heals.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import aiosqlite
import pytest

from stackowl.db.pool import DbPool, _looks_like_dead_sqlite


def _err(cls: type[sqlite3.Error], code: int, msg: str) -> sqlite3.Error:
    exc = cls(msg)
    # Python 3.11+ exposes the numeric sqlite error code on the exception.
    exc.sqlite_errorcode = code  # type: ignore[attr-defined]
    return exc


def test_missing_table_is_not_dead() -> None:
    """``no such table`` (SQLITE_ERROR) must NOT be classified as a dead handle."""
    exc = _err(sqlite3.OperationalError, sqlite3.SQLITE_ERROR, "no such table: ghost")
    assert _looks_like_dead_sqlite(exc) is False


def test_busy_is_dead() -> None:
    exc = _err(sqlite3.OperationalError, sqlite3.SQLITE_BUSY, "database is locked")
    assert _looks_like_dead_sqlite(exc) is True


def test_ioerr_is_dead() -> None:
    exc = _err(sqlite3.OperationalError, sqlite3.SQLITE_IOERR, "disk I/O error")
    assert _looks_like_dead_sqlite(exc) is True


def test_cantopen_is_dead() -> None:
    exc = _err(sqlite3.OperationalError, sqlite3.SQLITE_CANTOPEN, "unable to open db")
    assert _looks_like_dead_sqlite(exc) is True


def test_closed_connection_programming_error_is_dead() -> None:
    """aiosqlite raises ProgrammingError after .close(); that IS a dead handle."""
    exc = sqlite3.ProgrammingError("Cannot operate on a closed database.")
    assert _looks_like_dead_sqlite(exc) is True


def test_english_phrase_in_a_non_sqlite_error_is_not_dead() -> None:
    """A non-sqlite exception whose text happens to contain a marker phrase is
    NOT a dead handle — type-based classification, not substring."""
    exc = ValueError("query mentioned 'no such table' in a comment")
    assert _looks_like_dead_sqlite(exc) is False


@pytest.mark.asyncio
async def test_missing_table_surfaces_without_retry(tmp_path: Path) -> None:
    """A real missing-table query raises loudly and does NOT recycle the pool."""
    pool = DbPool(db_path=tmp_path / "x.db")
    await pool.open()
    with pytest.raises(aiosqlite.OperationalError) as ei:
        await pool.fetch_all("SELECT * FROM does_not_exist")
    assert "no such table" in str(ei.value)
    # No reconnect loop — the pool stays available, recycle count untouched.
    assert pool.available is True
    assert pool.recycle_count == 0
    await pool.close()


@pytest.mark.asyncio
async def test_real_dead_handle_still_self_heals(tmp_path: Path) -> None:
    """Closing the underlying connection (ProgrammingError on next use) still
    triggers exactly one recycle — the genuine dead-handle path is preserved."""
    pool = DbPool(db_path=tmp_path / "x.db")
    await pool.open()
    await pool.execute("CREATE TABLE t (id INTEGER)")
    assert pool._conn is not None
    await pool._conn.close()  # kill the handle out from under the pool
    await pool.execute("INSERT INTO t (id) VALUES (1)")
    rows = await pool.fetch_all("SELECT id FROM t")
    assert [r["id"] for r in rows] == [1]
    assert pool.recycle_count >= 1
    await pool.close()
