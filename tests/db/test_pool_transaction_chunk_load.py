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


def _patch_commit_delay(
    pool: DbPool, monkeypatch: pytest.MonkeyPatch
) -> list[float]:
    """Every commit() pays a fixed delay — models the real fsync cost that
    dominates writer-hold time (per pool.py:27-38).

    RETURNS THE COMMIT LOG, and that return value is the point of this change.
    The thing this test asserts is not a duration, it is a COUNT: how many
    commits the foreground write had to queue behind. See the test below.
    """
    assert pool._conn is not None
    orig_commit = pool._conn.commit
    commits: list[float] = []

    async def slow_commit() -> None:
        await asyncio.sleep(_COMMIT_DELAY_S)
        await orig_commit()
        commits.append(time.monotonic())

    monkeypatch.setattr(pool._conn, "commit", slow_commit)
    return commits


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
        commits = _patch_commit_delay(pool, monkeypatch)

        n_chunks = -(-_N_ROWS // _CHUNK_SIZE)  # ceil
        assert n_chunks == 6

        bg_start = time.monotonic()
        bg_task = asyncio.create_task(_run_chunked_background_job(pool, _N_ROWS))
        await asyncio.sleep(_COMMIT_DELAY_S / 2)  # let the job claim the writer first

        commits_before_fg = len(commits)
        fg_start = time.monotonic()
        await pool.execute("INSERT INTO fg (id, val) VALUES (?, ?)", (1, "fg"))
        fg_elapsed = time.monotonic() - fg_start
        queued_behind = len(commits) - commits_before_fg

        await bg_task
        bg_total_s = time.monotonic() - bg_start

        # THE PROPERTY IS A COUNT, NOT A DURATION, and stating it as a duration
        # is why this test has now been repaired twice.
        #
        # What chunked transactions buy is this: a foreground write queues
        # behind THE CHUNK IN FLIGHT, not behind the whole job. That is
        # countable. The first version asserted `fg_elapsed < _COMMIT_DELAY_S *
        # 3` — a measured duration against a constant — and failed in an
        # 11,973-test run while passing alone. The second (2026-09-05) made the
        # right diagnosis, that a constant does not inflate with load, and
        # reached for the wrong cure: `fg_elapsed < bg_total_s / 2` is still a
        # wall-clock RATIO, and it failed again on 2026-09-11 at
        # `0.450 < 0.790/2` on a box running the full suite.
        #
        # MEASURED 2026-09-11, seven consecutive runs: `queued_behind` was
        # **2, every time, with zero variance**, while `fg_elapsed` moved
        # 0.091-0.094s and `bg_total_s` moved 0.426-0.438s — and in the failing
        # full-suite run `bg_total_s` was 0.790s, nearly double, because the box
        # was loaded. The durations track the box. The count tracks the
        # BEHAVIOUR.
        #
        # Two is structural, not empirical: the background chunk already holding
        # the writer, then the foreground's own commit. A third would mean the
        # foreground queued behind a SECOND background chunk, which is precisely
        # the starvation this story fixed.
        assert queued_behind <= 2, (
            f"the foreground write queued behind {queued_behind} commits "
            f"(fg {fg_elapsed:.3f}s of the job's {bg_total_s:.3f}s). At most two "
            "are expected: the background chunk holding the writer, plus the "
            "foreground's own commit. More means a chunked background job is "
            "starving a concurrent write again."
        )

        # THE COMMIT COUNT IS BOUNDED ON BOTH SIDES, and the ceiling closes a
        # blind spot that BOTH earlier versions of this test had.
        #
        # The floor is the vacuity control: every assertion above passes if the
        # background job never ran, because zero commits is trivially <= 2.
        #
        # The CEILING is the one worth explaining. `pool.py:27-38` names the
        # failure mode this story fixed as "a chatty background write loop
        # committing one row at a time (one writer acquire/release/fsync per
        # row)". MEASURED 2026-09-11 by mutation: replacing the chunked job with
        # exactly that per-row loop PASSED the duration assertion, and passed the
        # floor too — 301 commits is comfortably >= 6. It passed the ORIGINAL
        # wall-clock assertion as well, because per-row autocommit releases the
        # writer between rows, so the foreground slips in fast while the job
        # crawls: `fg 0.09s < bg 15s / 2` is true and says nothing.
        #
        # The tell was never in the assertions — it was that the mutated run took
        # 17.2s instead of 0.6s. A test that takes 28x longer and still passes is
        # not measuring what its own docstring says it measures.
        #
        # A chunked job commits ONCE PER CHUNK. That is the whole claim, and it
        # is exact, so it is asserted exactly.
        assert n_chunks <= len(commits) <= n_chunks + 1, (
            f"{len(commits)} commits for a {n_chunks}-chunk job. Below {n_chunks} "
            "means the job did not run chunked at all (one unbounded transaction "
            "holding the writer); above it means one commit per row — the chatty "
            "loop pool.py:27-38 documents, which starves the foreground turn and "
            "the gateway heartbeat. The +1 is the foreground write's own commit."
        )

        fg_rows = await pool.fetch_all("SELECT id FROM fg")
        bg_rows = await pool.fetch_all("SELECT id FROM bg")
        assert len(fg_rows) == 1
        assert len(bg_rows) == _N_ROWS
    finally:
        await pool.close()
