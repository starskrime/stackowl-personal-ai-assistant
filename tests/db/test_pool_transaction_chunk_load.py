"""LAT.4 — DbPool.transaction() chunked-write regression guard.

pool.py:27-38 documents the failure mode this story fixes: a chatty
background write loop committing one row at a time (one writer
acquire/release/fsync per row) starves the foreground turn's writes and the
gateway process's liveness heartbeat. The fix is bounded chunked
transactions (this test simulates the pattern both
reflection_writer_handler.py and skills/store.py now use, directly against
the shared ``DbPool.transaction()`` primitive) instead of touching WAL mode,
busy_timeout, or any tick cadence (all explicitly out of scope).

Simulates the real cost the docstring names — "commit + WAL fsync + writer
acquire/release" per commit — by delaying ``commit()`` on the connection.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from stackowl.db.pool import DbPool

pytestmark = pytest.mark.asyncio

_CHUNK_SIZE = 50  # mirrors the bound (50-100) the story requires call sites to use
_COMMIT_DELAY_S = 0.05  # simulated slow commit/fsync — the cost pool.py's comment documents
_N_ROWS = 300


def _patch_commit_delay(pool: DbPool, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every commit() on the pool's connection pays a fixed delay — models the
    real fsync cost that dominates writer-hold time (per pool.py:27-38)."""
    assert pool._conn is not None
    orig_commit = pool._conn.commit

    async def slow_commit() -> None:
        await asyncio.sleep(_COMMIT_DELAY_S)
        await orig_commit()

    monkeypatch.setattr(pool._conn, "commit", slow_commit)


async def _run_chunked_background_job(pool: DbPool, n_rows: int) -> None:
    """Mirrors the pattern reflection_writer_handler.py / skills/store.py use:
    N rows committed in CHUNK_SIZE-bounded transactions via pool.transaction(),
    not one execute()-per-row autocommit and not one unbounded transaction."""
    for start in range(0, n_rows, _CHUNK_SIZE):
        end = min(start + _CHUNK_SIZE, n_rows)
        async with pool.transaction() as tx:
            for i in range(start, end):
                await tx.execute("INSERT INTO bg (id, val) VALUES (?, ?)", (i, "x"))


async def test_foreground_write_completes_quickly_during_chunked_background_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC #4's direct regression guard: a >=300-row background job committing
    in CHUNK_SIZE-bounded transactions must not block a concurrent foreground
    write for anywhere near the background job's total duration (6 chunks *
    the per-commit delay)."""
    pool = DbPool(db_path=tmp_path / "load_chunked.db")
    await pool.open()
    try:
        await pool.execute("CREATE TABLE bg (id INTEGER PRIMARY KEY, val TEXT)")
        await pool.execute("CREATE TABLE fg (id INTEGER PRIMARY KEY, val TEXT)")
        _patch_commit_delay(pool, monkeypatch)

        n_chunks = -(-_N_ROWS // _CHUNK_SIZE)  # ceil
        assert n_chunks == 6
        whole_job_span_s = n_chunks * _COMMIT_DELAY_S

        bg_start = time.monotonic()
        bg_task = asyncio.create_task(_run_chunked_background_job(pool, _N_ROWS))
        await asyncio.sleep(_COMMIT_DELAY_S / 2)  # let the job claim the writer first

        fg_start = time.monotonic()
        await pool.execute("INSERT INTO fg (id, val) VALUES (?, ?)", (1, "fg"))
        fg_elapsed = time.monotonic() - fg_start

        await bg_task
        bg_total_s = time.monotonic() - bg_start

        # Foreground must wait at most ~1-2 commit-delays (the chunk in
        # flight when it arrived, plus its own commit) — NEVER anywhere near
        # the whole job's span.
        assert fg_elapsed < _COMMIT_DELAY_S * 3
        assert fg_elapsed < whole_job_span_s / 2
        # Sanity: the background job really did take multiple commit-delays,
        # so the bound above is a meaningful, non-trivial assertion.
        assert bg_total_s >= _COMMIT_DELAY_S * (n_chunks - 1)

        fg_rows = await pool.fetch_all("SELECT id FROM fg")
        bg_rows = await pool.fetch_all("SELECT id FROM bg")
        assert len(fg_rows) == 1
        assert len(bg_rows) == _N_ROWS
    finally:
        await pool.close()
