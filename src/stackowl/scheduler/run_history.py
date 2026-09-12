"""What the scheduler actually DID — the table with 22,647 rows and no reader.

`GET /api/v1/schedules` renders 170 jobs from `jobs` alone: a definition, a cron
expression, an `enabled` flag and the status of the last attempt. Every one of
them looks identical to every other, and the surface cannot answer the question an
operator opens it to ask — *did the things I asked for actually happen?*

THE ANSWER WAS ALREADY BEING WRITTEN DOWN AND NOBODY READ IT. MEASURED
2026-09-12: `job_runs` holds **22,647 rows** spanning seven days, and the only
statements against it in `src/` are the scheduler's own `INSERT`, an idempotency
probe (`SELECT status … WHERE idempotency_key = ?`) and `db_reclaim`'s prune. Not
one reader treats it as history. That is the write-with-no-reader shape this repo
names first among its recurring six, sitting under the busiest object on the box.

WHAT IT SAYS, on this install, the day it was wired:

===========================================  =========
fact                                         value
===========================================  =========
runs in the last 24h                             5,188
of those, not `completed`                            0
enabled jobs with NO run in 2 days             **128** of 169
busiest job (`objective_driver`)              1,536 runs, 42ms typical
slowest job (`health_sweep`)                    415 runs, 1,058ms typical
===========================================  =========

The 128 is why this is worth a route. Three quarters of the enabled jobs have not
run in two days, and the schedules panel rendered them identically to the 41 that
had — because it was reading definitions and calling them activity.

**`status` IS A REAL FIELD WITH A CURRENTLY EMPTY FAILURE POPULATION, and that
distinction is load-bearing.** All 22,647 retained rows read `completed`; the
writer at `scheduler_mutations` line 230 is `status = "completed" if
result.success else "failed"`, so a failure CAN be recorded and none has been
inside the window. The count is reported rather than suppressed — but a guard
that only saw today's data would be vacuous, so the failure path is pinned by a
population the test CONSTRUCTS.

**NEITHER TABLE IS OWNER-GOVERNED.** Asked of the live schema rather than assumed:
`jobs` and `job_runs` both lack an `owner_id` column, and
`tests/tenancy/test_no_owner_scope_bypass.py` carries the control that says so in
as many words — `violations_in_statement("SELECT count(*) FROM job_runs") == []`.
So there is no per-owner predicate here and none is implied by its absence.

**AND IT DOES NOT READ `jobs`.** The job set has exactly one reader —
`Scheduler.list_jobs()`, which the cron tool and the route both already use, and
the route's own docstring records why: "a second reader of one table is how two
answers to one question get born." So this module answers only about `job_runs`,
keyed by `job_id`, and the caller joins. The join is three lines in the route and
it keeps both tables at one reader each.
"""

from __future__ import annotations

import datetime as _dt
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from stackowl.infra.observability import log

if TYPE_CHECKING:
    from stackowl.db.pool import DbPool

#: The window the surface reports. A day is the unit an operator thinks in
#: ("did it run today?") and it is well inside the seven-day retention below, so
#: a zero in this window is always a real zero rather than a pruned one.
DEFAULT_WINDOW_HOURS = 24

#: What `db_reclaim` keeps, named here so the horizon this module reports and the
#: sweep that creates it cannot disagree. Imported rather than re-stated: the
#: value moved once already on the operator's authority and two of its three
#: written copies went on quoting the old one for days.
def _retention_days() -> int:
    from stackowl.scheduler.handlers.db_reclaim import _RUN_HISTORY_RETENTION_DAYS

    return int(_RUN_HISTORY_RETENTION_DAYS)


@dataclass(frozen=True)
class JobRuns:
    """One job's behaviour inside the window. Keyed by `job_id`, never joined here."""

    runs: int
    failures: int
    last_ran_at: str | None
    typical_ms: int


@dataclass(frozen=True)
class RunHistoryGaps:
    """What this read could NOT see, so a surface can state its denominator.

    `horizon_at` is the half that stops a zero being ambiguous. `db_reclaim`
    prunes at `retention_days`, so "no runs" means *it did not run* only while
    the window sits inside the horizon — past that it means *we can no longer
    tell*. A surface that cannot draw that line reports a pruned job and a
    genuinely idle one the same way.
    """

    window_hours: int
    retention_days: int
    horizon_at: str | None
    total_runs: int
    total_failures: int


async def read_run_history(
    db: DbPool,
    *,
    window_hours: int = DEFAULT_WINDOW_HOURS,
) -> tuple[dict[str, JobRuns], RunHistoryGaps]:
    """Per-job run facts for the window, plus what the window could not see.

    ONE STATEMENT FOR THE SET and one for the horizon. `status <> 'completed'` is
    counted rather than `= 'failed'` so a third word added to the writer is
    counted as a failure rather than silently dropped into neither column — the
    fail-safe direction, since the alternative under-reports trouble.
    """
    t0 = time.monotonic()
    log.scheduler.info(
        "[scheduler] run_history.read: entry",
        extra={"_fields": {"window_hours": window_hours}},
    )

    # THE THRESHOLD IS AN ISO STRING, NOT `datetime('now', ?)`, AND THE
    # DIFFERENCE IS ONE CHARACTER THAT SILENTLY WIDENS THE WINDOW.
    #
    # Every timestamp column in this database stores ISO-8601 with a `T` and an
    # offset — `2026-09-05T00:00:50.784648+00:00`. SQLite's own `datetime()`
    # returns `2026-09-05 15:07:19`, space-separated. Compared as STRINGS, `'T'`
    # (0x54) sorts after `' '` (0x20), so **every row sharing the bound's date
    # passes the filter whatever its clock says**. MEASURED on the first test that
    # constructed a 30-hour-old run: it was counted inside a 24-hour window.
    #
    # It hid on live data because most rows genuinely are recent — the count was
    # 5,195 against a true 5,188, close enough to read as noise. A comparison that
    # is wrong only near the boundary is the kind nothing notices.
    cutoff = (
        _dt.datetime.now(_dt.UTC) - _dt.timedelta(hours=float(window_hours))
    ).isoformat()

    rows = await db.fetch_all(
        "SELECT job_id, COUNT(*) AS runs, "
        "SUM(CASE WHEN status <> 'completed' THEN 1 ELSE 0 END) AS failures, "
        "MAX(ran_at) AS last_ran_at, "
        "CAST(ROUND(AVG(duration_ms)) AS INTEGER) AS typical_ms "
        "FROM job_runs WHERE ran_at > ? "
        "GROUP BY job_id",
        (cutoff,),
    )

    by_job: dict[str, JobRuns] = {}
    for r in rows:
        by_job[str(r["job_id"])] = JobRuns(
            runs=int(r["runs"] or 0),
            failures=int(r["failures"] or 0),
            last_ran_at=(str(r["last_ran_at"]) if r["last_ran_at"] else None),
            typical_ms=int(r["typical_ms"] or 0),
        )

    horizon = await db.fetch_all("SELECT MIN(ran_at) AS oldest FROM job_runs")
    oldest = (
        str(horizon[0]["oldest"])
        if horizon and horizon[0]["oldest"] is not None
        else None
    )

    gaps = RunHistoryGaps(
        window_hours=int(window_hours),
        retention_days=_retention_days(),
        horizon_at=oldest,
        total_runs=sum(j.runs for j in by_job.values()),
        total_failures=sum(j.failures for j in by_job.values()),
    )

    duration_ms = (time.monotonic() - t0) * 1000
    log.scheduler.info(
        "[scheduler] run_history.read: exit — served",
        extra={"_fields": {
            "jobs_with_runs": len(by_job),
            "total_runs": gaps.total_runs,
            "total_failures": gaps.total_failures,
            "window_hours": gaps.window_hours,
            "duration_ms": duration_ms,
        }},
    )
    return by_job, gaps
