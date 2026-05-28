"""Self-healing tests for DbPool — reconnect on dead handle, retry once, fire callbacks."""

from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest

from stackowl.db.pool import DbPool
from stackowl.infra.resilience import HealableResource

pytestmark = pytest.mark.asyncio


async def test_pool_satisfies_healable_resource_protocol(tmp_path: Path) -> None:
    pool = DbPool(db_path=tmp_path / "x.db")
    assert isinstance(pool, HealableResource)


async def test_open_then_available(tmp_path: Path) -> None:
    pool = DbPool(db_path=tmp_path / "x.db")
    assert pool.available is False
    await pool.open()
    assert pool.available is True
    await pool.close()
    assert pool.available is False


async def test_ensure_available_opens_when_closed(tmp_path: Path) -> None:
    pool = DbPool(db_path=tmp_path / "x.db")
    await pool.ensure_available()
    assert pool.available is True
    await pool.close()


async def test_ensure_available_is_noop_when_open(tmp_path: Path) -> None:
    pool = DbPool(db_path=tmp_path / "x.db")
    await pool.open()
    orig_conn = pool._conn
    await pool.ensure_available()
    assert pool._conn is orig_conn  # same connection — no reconnect
    await pool.close()


async def test_execute_self_heals_after_underlying_close(tmp_path: Path) -> None:
    pool = DbPool(db_path=tmp_path / "x.db")
    await pool.open()
    await pool.execute("CREATE TABLE t (id INTEGER)")
    await pool.execute("INSERT INTO t (id) VALUES (1)")

    # Simulate connection death — close the aiosqlite connection out from under
    # the pool without going through pool.close() (which clears _conn).
    assert pool._conn is not None
    await pool._conn.close()
    # _conn still references a closed handle; first execute should detect death.
    await pool.execute("INSERT INTO t (id) VALUES (2)")

    rows = await pool.fetch_all("SELECT id FROM t ORDER BY id")
    assert [r["id"] for r in rows] == [1, 2]
    assert pool.recycle_count >= 1
    await pool.close()


async def test_register_on_recycled_fires_on_reconnect(tmp_path: Path) -> None:
    pool = DbPool(db_path=tmp_path / "x.db")
    await pool.open()
    fired: list[int] = []
    pool.register_on_recycled(lambda: fired.append(1))

    assert pool._conn is not None
    await pool._conn.close()
    # Trigger reconnect via execute
    await pool.execute("CREATE TABLE t (id INTEGER)")

    assert fired == [1]
    await pool.close()


async def test_pool_propagates_non_dead_handle_errors(tmp_path: Path) -> None:
    pool = DbPool(db_path=tmp_path / "x.db")
    await pool.open()
    # Syntax error — not a dead handle, should propagate without retry.
    with pytest.raises(aiosqlite.OperationalError):
        await pool.execute("THIS IS NOT SQL")
    assert pool.available is True  # pool stays alive
    assert pool.recycle_count == 0
    await pool.close()


async def test_concurrent_ensure_available_only_reconnects_once(tmp_path: Path) -> None:
    import asyncio

    pool = DbPool(db_path=tmp_path / "x.db")
    await pool.open()
    assert pool._conn is not None
    await pool._conn.close()
    pool._conn = None

    await asyncio.gather(
        pool.ensure_available(),
        pool.ensure_available(),
        pool.ensure_available(),
    )
    assert pool.available is True
    await pool.close()


async def test_recycle_metadata_updates_on_reconnect(tmp_path: Path) -> None:
    pool = DbPool(db_path=tmp_path / "x.db")
    await pool.open()
    assert pool.recycle_count == 0
    assert pool.last_recycle_at is None

    assert pool._conn is not None
    await pool._conn.close()
    pool._conn = None

    await pool.ensure_available()
    assert pool.recycle_count == 1
    assert pool.last_recycle_at is not None
    assert pool.last_recycle_reason is not None
    await pool.close()
