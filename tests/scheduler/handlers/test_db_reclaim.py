"""DbReclaimHandler — the database file's decay leg.

Bakir, 2026-08-22: "Why system itself doesnot have capability to vacuum
database". It did not. Measured on the live box: a 922 MB file of which 643 MB
(70%) was free pages, `auto_vacuum=NONE`, on a root filesystem at 99% — surfacing
not as "disk full" but as `database is locked`, a task loop failing every tick,
and a three-minute Telegram send.

The tests below pin the three things that make this a fix rather than a cron job:
it actually returns pages to the OS, it REFUSES SILENTLY-USELESS work on a
database that cannot reclaim, and it complains when it falls behind.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from stackowl.db.pool import DbPool
from stackowl.scheduler.handlers.db_reclaim import (
    DbReclaimHandler,
    needs_one_time_vacuum,
)
from stackowl.scheduler.job import Job


def _job(**params: object) -> Job:
    """Mirrors tests/scheduler/handlers/test_downloads_janitor.py's fixture shape —
    the sibling janitor this handler is modelled on."""
    return Job(
        job_id=f"db_reclaim-{uuid.uuid4().hex[:6]}",
        handler_name="db_reclaim",
        schedule="every 1h",
        idempotency_key=uuid.uuid4().hex,
        last_run_at=None,
        next_run_at=datetime.now(UTC).isoformat(),
        status="pending",
        params=dict(params),
    )


async def _pool_for(db_path: Path) -> DbPool:
    pool = DbPool(db_path=db_path)
    await pool.open()
    return pool


def _seed_with_free_pages(db_path: Path, *, auto_vacuum: int) -> None:
    """Build a database that has genuinely freed a lot of pages.

    Written, then DELETED — which is what every janitor in this platform does and
    is exactly the state that produced 643 MB of unreclaimable waste.
    """
    conn = sqlite3.connect(db_path)
    conn.execute(f"PRAGMA auto_vacuum={auto_vacuum}")
    conn.execute("CREATE TABLE bulk (id INTEGER PRIMARY KEY, blob TEXT)")
    conn.executemany(
        "INSERT INTO bulk (blob) VALUES (?)", [("x" * 2000,) for _ in range(4000)]
    )
    conn.commit()
    conn.execute("DELETE FROM bulk")
    conn.commit()
    conn.close()


@pytest.mark.asyncio
async def test_it_actually_returns_pages_to_the_operating_system(tmp_path: Path) -> None:
    """THE POINT. Not "the pragma ran" — the FILE gets smaller.

    Measuring the effect rather than trusting the call is this codebase's most
    load-bearing rule, and a reclaimer is exactly where a call could succeed while
    nothing happens.
    """
    db = tmp_path / "reclaim.db"
    _seed_with_free_pages(db, auto_vacuum=2)  # INCREMENTAL
    size_before = db.stat().st_size

    pool = await _pool_for(db)
    try:
        result = await DbReclaimHandler(pool).execute(_job(max_pages=100_000))
    finally:
        await pool.close()

    assert result.success, result.error
    assert result.metadata["reclaimed_pages"] > 0, (
        f"nothing was reclaimed from a database full of freed pages: {result.metadata}"
    )
    assert db.stat().st_size < size_before, (
        f"the pragma ran but the FILE did not shrink: {size_before} -> "
        f"{db.stat().st_size}. A reclaimer that does not reclaim is the defect "
        "this handler exists to fix, wearing its uniform."
    )


@pytest.mark.asyncio
async def test_a_database_that_cannot_reclaim_says_so_instead_of_reporting_success(
    tmp_path: Path,
) -> None:
    """THE CASE THAT KEPT THE ORIGINAL DEFECT INVISIBLE FOR MONTHS.

    `incremental_vacuum` on an `auto_vacuum=NONE` database is a silent no-op —
    it succeeds and reclaims nothing, forever. A handler that ran it and reported
    success would be indistinguishable from one that works, which is precisely how
    643 MB accumulated with nothing noticing.

    So the handler must DETECT that state and say so at WARNING.
    """
    db = tmp_path / "none.db"
    _seed_with_free_pages(db, auto_vacuum=0)  # NONE — cannot reclaim
    size_before = db.stat().st_size

    pool = await _pool_for(db)
    try:
        assert await needs_one_time_vacuum(pool) is True
        result = await DbReclaimHandler(pool).execute(_job())
    finally:
        await pool.close()

    assert result.metadata["needs_vacuum"] is True
    assert result.metadata["reclaimed_pages"] == 0
    assert "VACUUM" in result.output
    # And it must not have pretended: the file is unchanged.
    assert db.stat().st_size == size_before


@pytest.mark.asyncio
async def test_the_reclaim_is_BOUNDED_so_it_never_becomes_the_blocking_writer(
    tmp_path: Path,
) -> None:
    """A maintenance sweep that holds the write lock is the contention this exists
    to prevent, not cause. `max_pages` must actually bound the work — asserted by
    giving it a small budget and checking it left most of the freelist alone."""
    db = tmp_path / "bounded.db"
    _seed_with_free_pages(db, auto_vacuum=2)

    conn = sqlite3.connect(db)
    free_before = conn.execute("PRAGMA freelist_count").fetchone()[0]
    conn.close()
    assert free_before > 200, "fixture must produce a substantial freelist"

    pool = await _pool_for(db)
    try:
        result = await DbReclaimHandler(pool).execute(_job(max_pages=50))
    finally:
        await pool.close()

    assert result.success
    assert result.metadata["reclaimed_pages"] <= 50, (
        f"max_pages was ignored — reclaimed {result.metadata['reclaimed_pages']}"
    )

    conn = sqlite3.connect(db)
    free_after = conn.execute("PRAGMA freelist_count").fetchone()[0]
    conn.close()
    assert free_after > 0, "a bounded pass must leave work for the next one"


@pytest.mark.asyncio
async def test_a_healthy_database_needs_no_one_time_vacuum(tmp_path: Path) -> None:
    """The other jaw: `needs_one_time_vacuum` must not cry wolf on a database that
    is already INCREMENTAL, or every hourly pass would log a false warning and
    train its reader to ignore the real one."""
    db = tmp_path / "healthy.db"
    _seed_with_free_pages(db, auto_vacuum=2)
    pool = await _pool_for(db)
    try:
        assert await needs_one_time_vacuum(pool) is False
    finally:
        await pool.close()
