"""The code wrote down its own expiry condition, and nobody re-read it when it fired.

`ApproachRatingTracker` carried a note explaining why its table needed no bound:

    "no size cap / TTL sweep — a DB row is small (short text + two ints) and rare
     (one per qualifying Telegram answer, cleared on tap), so an unbounded table
     is fine for now. Add a periodic delete-older-than-N-days sweep IF UNTAPPED
     VOTES EVER ACCUMULATE."

MEASURED 2026-09-10: **1,480 pending rows spanning 2026-07-13 to today**, against
**61 ratings ever recorded**. "Cleared on tap" clears 4% of what it writes; 1,425 of
those rows are older than a week. The condition the note named to invalidate itself
had been true for two months and nothing re-read it.

WHY IT COULD SIT THERE. The premise — "rare, and cleared on tap" — was never
measured, and nothing anywhere compares a table's growth against the reason it was
allowed to grow. This is the SECOND instance of that shape in this session; the
first was a page that recorded its own expiry condition and went on being used after
it fired.

THE WINDOW IS DERIVED, NOT CHOSEN. Every tap in the retained corpus was timed
against its own answer's `captured_at`: 13 taps, maximum latency **3m32s**. Not one
tap in 1,541 offers arrived more than four minutes after the offer, so a row a week
old has never been tapped in the platform's history.

AND THE SWEEP IS OWNER-SCOPED, which is only assertable because of DEBT-291.
`approach_rating_pending` carries `owner_id` and became visible to the owner-scope
tripwire the day before this shipped. On a single-principal install an unscoped
DELETE and a scoped one are indistinguishable — which is exactly how an unscoped one
survives review.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from stackowl.db.pool import DbPool
from stackowl.scheduler.handlers.db_reclaim import (
    _RATING_PENDING_RETENTION_DAYS,
    DbReclaimHandler,
)

_DAY = 86_400.0


def _seed(db_path: Path, rows: list[tuple[str, str, float]]) -> None:
    """(trace_id, owner_id, created_at) — the three columns this sweep reads."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE approach_rating_pending ("
        "  trace_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, text TEXT NOT NULL,"
        "  chat_id INTEGER, message_id INTEGER, created_at REAL NOT NULL)"
    )
    conn.execute("CREATE TABLE principals (principal_id TEXT PRIMARY KEY)")
    for _, owner, _ in rows:
        conn.execute(
            "INSERT OR IGNORE INTO principals (principal_id) VALUES (?)", (owner,)
        )
    conn.executemany(
        "INSERT INTO approach_rating_pending "
        "(trace_id, owner_id, text, chat_id, message_id, created_at) "
        "VALUES (?, ?, 'answer', NULL, NULL, ?)",
        [(t, o, ts) for t, o, ts in rows],
    )
    conn.commit()
    conn.close()


async def _pool_for(db_path: Path) -> DbPool:
    pool = DbPool(db_path=db_path)
    await pool.open()
    return pool


@pytest.mark.asyncio
async def test_a_row_past_the_window_goes_and_a_fresh_one_stays(tmp_path: Path) -> None:
    """The whole point in one assertion: bounded, not emptied.

    A sweep that deleted everything would pass any "the table shrank" check and
    destroy the votes still reachable — every tap in the corpus landed inside four
    minutes, so the fresh rows are precisely the ones that can still be answered.
    """
    now = time.time()
    db = tmp_path / "t.db"
    _seed(db, [
        ("old", "principal-default", now - (_RATING_PENDING_RETENTION_DAYS + 1) * _DAY),
        ("new", "principal-default", now - 60),
    ])
    pool = await _pool_for(db)
    try:
        deleted = await DbReclaimHandler(pool)._prune_untapped_ratings()  # noqa: SLF001
        rows = await pool.fetch_all("SELECT trace_id FROM approach_rating_pending", ())
    finally:
        await pool.close()

    assert deleted == 1
    assert [r["trace_id"] for r in rows] == ["new"], (
        "either an expired row survived or a live one was swept — and the live one "
        "is the only kind anybody has ever tapped"
    )


@pytest.mark.asyncio
async def test_the_sweep_never_reaches_another_owners_rows(tmp_path: Path) -> None:
    """THE ASSERTION THAT ONLY DEBT-291 MADE POSSIBLE TO MAKE.

    The tenancy tripwire reads SQL text and would accept any statement carrying an
    `owner_id` predicate. This reads BEHAVIOUR: two owners, both with expired rows,
    and only the ones belonging to an owner the tenancy store actually holds may be
    touched — the sweep iterates principals rather than deleting by time alone.
    """
    now = time.time()
    stale = now - (_RATING_PENDING_RETENTION_DAYS + 1) * _DAY
    db = tmp_path / "t.db"
    _seed(db, [("a-old", "principal-default", stale), ("b-old", "principal-other", stale)])

    # `principal-other` is a row in the table but NOT in the tenancy store — the
    # shape a renamed or removed principal leaves behind.
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM principals WHERE principal_id = 'principal-other'")
    conn.commit()
    conn.close()

    pool = await _pool_for(db)
    try:
        deleted = await DbReclaimHandler(pool)._prune_untapped_ratings()  # noqa: SLF001
        rows = await pool.fetch_all("SELECT trace_id FROM approach_rating_pending", ())
    finally:
        await pool.close()

    assert deleted == 1
    assert [r["trace_id"] for r in rows] == ["b-old"], (
        "the sweep deleted rows for an owner the tenancy store does not hold — a "
        "maintenance job is not an exemption from the tenancy boundary"
    )


@pytest.mark.asyncio
async def test_an_empty_or_unreadable_principals_table_deletes_nothing(
    tmp_path: Path,
) -> None:
    """"An empty table is a QUESTION, not an answer." With no principals readable
    the sweep must delete NOTHING rather than fall back to deleting by time — the
    fail-open direction here is to leave the operator's rows alone."""
    now = time.time()
    db = tmp_path / "t.db"
    _seed(db, [("old", "principal-default",
                now - (_RATING_PENDING_RETENTION_DAYS + 1) * _DAY)])
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM principals")
    conn.commit()
    conn.close()

    pool = await _pool_for(db)
    try:
        deleted = await DbReclaimHandler(pool)._prune_untapped_ratings()  # noqa: SLF001
        rows = await pool.fetch_all("SELECT COUNT(*) AS n FROM approach_rating_pending", ())
    finally:
        await pool.close()

    assert deleted == 0
    assert rows[0]["n"] == 1, "rows were deleted with no principal to attribute them to"


@pytest.mark.asyncio
async def test_a_failure_never_fails_the_tick(tmp_path: Path) -> None:
    """Maintenance may not fail a scheduler tick. No table at all is the bluntest
    version of that, and it must return 0 rather than raise."""
    db = tmp_path / "t.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE principals (principal_id TEXT PRIMARY KEY)")
    conn.execute("INSERT INTO principals VALUES ('principal-default')")
    conn.commit()
    conn.close()

    pool = await _pool_for(db)
    try:
        assert await DbReclaimHandler(pool)._prune_untapped_ratings() == 0  # noqa: SLF001
    finally:
        await pool.close()


@pytest.mark.tripwire
def test_the_window_is_the_one_the_measurement_supports() -> None:
    """THE CONTROL. Seven days is ~2,800x the longest tap ever observed (3m32s),
    unchanged since MEASURED 2026-09-10 -- pinned directly rather than against a
    neighbour, for the reason below.

    IT USED TO MATCH `job_runs`'s WINDOW BESIDE IT, so this codebase carried ONE
    retention number rather than two -- and this test originally enforced that
    equality, with the instruction that whoever moved either value had to say
    what evidence they used.

    STORY 2.11 (DW-17) IS THAT STATED REASON -- but NOT via AD-4 reaching
    `job_runs` directly: it does not. No journal event references a
    `job_runs` row (`journal/coverage.py` marks it `_REASON_PRE_EPOCH`,
    unjournaled -- the same excuse `approach_rating_pending` carries), and
    `job.*` events' `record_ref` points at the DIFFERENT `jobs` table
    (`journal/job_events.py`'s `_TABLE`), not `job_runs`. The real reason:
    the pre-existing `tests/journal/test_retention_tripwire.py` (Story 2.6)
    already checked `_RUN_HISTORY_RETENTION_DAYS` against journal retention,
    and DW-17 assigned Story 2.11 to reconcile every window that tripwire
    checks -- raised 7 -> 30 in the same change that raised
    `JournalSettings.retention_days` to 30. `approach_rating_pending` is not
    named by that tripwire at all, so it was not touched: raising it anyway
    would grow a table nothing requires grown for no measured reason --
    exactly the kind of change this programme's own rules treat as a
    stop-and-brief. Pinned separately now so a later, UNEXPLAINED drift of
    either value still fails loudly."""
    from stackowl.scheduler.handlers import db_reclaim

    assert _RATING_PENDING_RETENTION_DAYS == 7
    assert db_reclaim._RUN_HISTORY_RETENTION_DAYS == 30  # noqa: SLF001
