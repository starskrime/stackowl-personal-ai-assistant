"""`job_runs` had 22,647 rows and no reader — this is the reader, and its guards.

MEASURED 2026-09-12. `GET /api/v1/schedules` rendered 170 jobs from the `jobs`
table alone: a definition, a cron expression, an `enabled` flag. Every job looked
identical to every other, and the surface could not answer the question an
operator opens it to ask — *did the things I asked for actually happen?*

THE ANSWER WAS ALREADY BEING WRITTEN DOWN. `job_runs` carries `ran_at`, `status`
and `duration_ms`, and the only statements against it anywhere in `src/` were the
scheduler's own `INSERT`, an idempotency probe keyed on `idempotency_key`, and
`db_reclaim`'s prune. Nothing read it as history. That is the write-with-no-reader
shape this repo names first among its six, sitting under the busiest object on the
box — and what it says is not decoration:

* 5,195 runs in the last 24 hours,
* **131 of 169 enabled jobs had not run at all** in that window,
* which the panel rendered exactly like the 38 that had.

THE HARD PART IS NOT THE QUERY, IT IS THAT THE INTERESTING POPULATION IS EMPTY.
All 22,647 retained rows read `completed`. So a test written against live-shaped
data would assert `failures == 0` and pass for the rest of time whether the
failure path worked or not — the vacuity this tree has now paid for in DEBT-299,
DEBT-300, DEBT-311 and DEBT-314. Every assertion about failure here is therefore
made against rows this file INSERTS.
"""

from __future__ import annotations

import datetime as _dt
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

from stackowl.db.pool import DbPool
from stackowl.scheduler.run_history import (
    DEFAULT_WINDOW_HOURS,
    read_run_history,
)
from tests._schema_template import seed_schema

pytestmark = pytest.mark.asyncio


@pytest.fixture()
async def pool(tmp_path: Path) -> AsyncGenerator[DbPool]:
    db_path = tmp_path / "runs.db"
    seed_schema(db_path)
    p = DbPool(db_path=db_path)
    await p.open()
    try:
        yield p
    finally:
        await p.close()


def _ago(hours: float) -> str:
    return (
        _dt.datetime.now(_dt.UTC) - _dt.timedelta(hours=hours)
    ).isoformat()


async def _job(pool: DbPool, job_id: str, *, enabled: int = 1) -> None:
    """`job_runs.job_id` is a FOREIGN KEY into `jobs` (migration 0080, so a job
    removal cascades its history away). A run cannot exist without its job, and a
    fixture that inserted one anyway would be modelling a database this schema
    forbids — which is the stopped-resembling-the-real-thing shape this repo names
    second among its six."""
    await pool.execute(
        "INSERT OR IGNORE INTO jobs (job_id, handler_name, schedule, "
        "idempotency_key, next_run_at, status, enabled, created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (job_id, "handler", "*/5 * * * *", f"key-{job_id}", _ago(0),
         "pending", enabled, _ago(0)),
    )


async def _run(
    pool: DbPool, job_id: str, *, status: str = "completed",
    hours_ago: float = 1.0, ms: int = 10, run_id: str | None = None,
) -> None:
    await _job(pool, job_id)
    await pool.execute(
        "INSERT INTO job_runs (run_id, job_id, idempotency_key, status, "
        "duration_ms, ran_at) VALUES (?,?,?,?,?,?)",
        (run_id or f"{job_id}-{hours_ago}-{status}-{ms}", job_id,
         f"key-{job_id}", status, ms, _ago(hours_ago)),
    )


class TestItReadsWhatTheJobsDid:
    async def test_a_job_that_ran_is_counted_with_its_typical_duration(
        self, pool: DbPool
    ) -> None:
        await _run(pool, "sweep", ms=10, hours_ago=1)
        await _run(pool, "sweep", ms=30, hours_ago=2)

        by_job, gaps = await read_run_history(pool)

        assert set(by_job) == {"sweep"}
        assert by_job["sweep"].runs == 2
        assert by_job["sweep"].typical_ms == 20
        assert gaps.total_runs == 2

    async def test_a_job_that_did_NOT_run_is_ABSENT_rather_than_zero(
        self, pool: DbPool
    ) -> None:
        """The distinction the whole surface turns on.

        This module deliberately does not read `jobs` — that table has exactly
        one reader (`list_jobs`) and a second one is how two answers to one
        question get born. So a silent job cannot appear here at all, and the
        CALLER learns it is silent by finding its id missing. A reader that
        invented a zero row for it would be reading `jobs` by another name.
        """
        await _run(pool, "busy")

        by_job, _ = await read_run_history(pool)

        assert "busy" in by_job
        assert "silent" not in by_job

    async def test_a_run_OUTSIDE_the_window_does_not_count(self, pool: DbPool) -> None:
        await _run(pool, "old", hours_ago=DEFAULT_WINDOW_HOURS + 6)
        await _run(pool, "new", hours_ago=1)

        by_job, gaps = await read_run_history(pool)

        assert set(by_job) == {"new"}
        assert gaps.total_runs == 1

    async def test_the_window_is_a_PARAMETER_and_actually_moves(
        self, pool: DbPool
    ) -> None:
        """The control for the test above: without it, "old is excluded" cannot
        be told from a query that excludes everything."""
        await _run(pool, "old", hours_ago=DEFAULT_WINDOW_HOURS + 6)

        narrow, _ = await read_run_history(pool)
        wide, gaps = await read_run_history(
            pool, window_hours=DEFAULT_WINDOW_HOURS * 4
        )

        assert "old" not in narrow
        assert "old" in wide
        assert gaps.window_hours == DEFAULT_WINDOW_HOURS * 4


class TestTheFailurePopulationIsCONSTRUCTED:
    """Every assertion here is about rows this file inserts, and that is the point.

    All 22,647 rows on the live install read `completed`, so a test shaped like
    production data would assert `failures == 0` and go on passing whether the
    failure path worked or not. `scheduler_mutations` writes
    `status = "completed" if result.success else "failed"`, so the path is real
    and merely unexercised inside the retention window.
    """

    async def test_a_FAILED_run_is_counted_as_a_failure(self, pool: DbPool) -> None:
        await _run(pool, "flaky", status="completed")
        await _run(pool, "flaky", status="failed", hours_ago=2)

        by_job, gaps = await read_run_history(pool)

        assert by_job["flaky"].runs == 2
        assert by_job["flaky"].failures == 1
        assert gaps.total_failures == 1

    async def test_a_status_NOBODY_HAS_WRITTEN_YET_counts_as_a_failure(
        self, pool: DbPool
    ) -> None:
        """`<> 'completed'` rather than `= 'failed'`, and the direction matters.

        A third word added to the writer — `timeout`, `abandoned` — lands in
        neither column under an equality test, so the surface would report a run
        that did not complete as a clean one. Counting the complement fails SAFE:
        an unrecognised outcome is trouble until somebody says otherwise.
        """
        await _run(pool, "odd", status="timeout")

        by_job, gaps = await read_run_history(pool)

        assert by_job["odd"].failures == 1, (
            "an unrecognised status was counted as a success — the equality test "
            "this guards against under-reports trouble by construction"
        )
        assert gaps.total_failures == 1

    async def test_an_ALL_GREEN_window_really_does_report_zero(
        self, pool: DbPool
    ) -> None:
        """The other direction, so the failure count is not simply always non-zero."""
        await _run(pool, "fine")
        await _run(pool, "fine", hours_ago=3)

        by_job, gaps = await read_run_history(pool)

        assert by_job["fine"].failures == 0
        assert gaps.total_failures == 0


class TestItStatesWhatItCannotSee:
    async def test_the_HORIZON_is_reported_so_a_zero_is_never_ambiguous(
        self, pool: DbPool
    ) -> None:
        """`db_reclaim` prunes this table, so "no runs" means *idle* only while
        the window sits inside what is still kept. Past the horizon it means
        *nobody can tell any more*, and a surface that cannot draw that line
        reports a pruned job and a genuinely idle one identically."""
        await _run(pool, "a", hours_ago=100)
        await _run(pool, "b", hours_ago=1)

        _, gaps = await read_run_history(pool)

        assert gaps.horizon_at is not None
        assert gaps.horizon_at < _ago(50), (
            f"the horizon is not the OLDEST retained run: {gaps.horizon_at}"
        )

    async def test_an_EMPTY_table_answers_rather_than_raising(
        self, pool: DbPool
    ) -> None:
        """A fresh install has no history, and that is not an error."""
        by_job, gaps = await read_run_history(pool)

        assert by_job == {}
        assert gaps.horizon_at is None
        assert gaps.total_runs == 0
        assert gaps.total_failures == 0

    async def test_the_retention_is_ASKED_of_the_sweep_that_creates_it(self) -> None:
        """Not restated. The value moved once already on the operator's authority
        and two of its three written copies went on quoting the old one for days
        — `db_reclaim`'s own record of that is why this is an import."""
        from stackowl.scheduler.handlers import db_reclaim
        from stackowl.scheduler.run_history import _retention_days

        assert _retention_days() == db_reclaim._RUN_HISTORY_RETENTION_DAYS  # noqa: SLF001


@pytest.mark.tripwire
async def test_job_runs_has_EXACTLY_ONE_HISTORY_READER(pool: DbPool) -> None:
    """The reason this module exists rather than a query in the route.

    `jobs` has one reader (`Scheduler.list_jobs`) and the schedules route's own
    docstring records why: "a second reader of one table is how two answers to one
    question get born." `job_runs` now has one history reader, and the join
    between them happens in Python where both stay single.

    Scoped to HISTORY reads: the scheduler's idempotency probe and `db_reclaim`'s
    prune are different questions over the same table and are named here so the
    exemption is explicit rather than accidental.
    """
    import re

    src = Path(__file__).resolve().parents[2] / "src" / "stackowl"
    allowed = {
        "scheduler/run_history.py",      # this module — the history reader
        "scheduler/scheduler.py",        # idempotency probe + the INSERT
        "scheduler/scheduler_mutations.py",  # the other INSERT
        "scheduler/assembly.py",         # cascade delete on job removal
        "scheduler/handlers/db_reclaim.py",  # the prune
    }
    offenders: list[str] = []
    for path in sorted(src.rglob("*.py")):
        rel = str(path.relative_to(src))
        if rel in allowed:
            continue
        if re.search(r"\bFROM\s+job_runs\b", path.read_text(encoding="utf-8")):
            offenders.append(rel)

    assert not offenders, (
        f"a second reader of `job_runs` appeared in {offenders} — two readers of "
        "one table is how two answers to one question get born. Extend "
        "`run_history.py` instead."
    )
    assert (src / "scheduler" / "run_history.py").exists(), (
        "the history reader is gone and this guard is now checking nobody"
    )
