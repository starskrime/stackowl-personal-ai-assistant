"""`jobs` is dated by the column a RUN writes, not the one a creation writes.

MEASURED 2026-09-04 on the live platform. The health sweep logged
`[scheduler] health_sweep.execute: UNHEALTHY subsystems detected` with
`degraded: ["store_cadence"]` **31 times in one boot**, and raised an incident
signature `health:store_cadence:degraded`. The store it named was `jobs`:

    idle_days = 1.22, allowed_days = 1.0
    why = "a live loop writes this on ordinary traffic"

That reason is true, and the column was wrong. A job ROW is created once, when
the job is scheduled; the live loop writes `last_run_at` on every run. At the
moment of the alarm `jobs.created_at` was 1.2 days old and `jobs.last_run_at` was
TWENTY-TWO SECONDS old — the scheduler was running perfectly and being reported
unhealthy for it.

WHAT THE FIX BUYS BEYOND SILENCE. Dating `jobs` by `created_at` made the check
mean "has anyone scheduled a new job lately", which nothing depends on. Dating it
by `last_run_at` makes it mean "is the scheduler still running", which is worth an
alarm. The check goes from a false positive to a real detector.

NOT A GENERAL COLUMN SWEEP. Two other hot stores also declare `created_at` while
carrying a fresher `updated_at` — `message_ledger` and `delivery_attempts` — and
both were within SECONDS of each other when measured, so neither can produce this
failure. They are left alone deliberately: changing them would be churn, and the
defect here is specific to a table whose rows are created rarely and written often.
"""

from __future__ import annotations

import sqlite3

from stackowl.health.store_cadence import Cadence, declaration_for


def test_jobs_is_dated_by_the_run_not_the_creation() -> None:
    """The fix, asserted directly."""
    decl = declaration_for("jobs")
    assert decl is not None
    assert decl.clock == "last_run_at", (
        "jobs is dated by a column a running scheduler does not write, so a "
        "healthy platform reports itself unhealthy"
    )


def test_jobs_is_still_declared_HOT() -> None:
    """The cadence tier is right and was never the problem — only the column.

    Vacuity control: reclassifying `jobs` as on-demand would also silence the
    alarm, and would throw away the signal that the scheduler has stopped.
    """
    decl = declaration_for("jobs")
    assert decl is not None
    assert decl.cadence is Cadence.HOT


def test_the_column_exists_on_the_real_schema() -> None:
    """A declared clock that the table does not have is unmeasurable, silently.

    This is the check the original defect would have survived: `created_at` DOES
    exist on `jobs`, so nothing complained — it was simply the wrong one of two
    real columns.
    """
    import tempfile
    from pathlib import Path

    from stackowl.db.migrations.runner import MigrationRunner

    path = Path(tempfile.mkdtemp()) / "t.db"
    MigrationRunner(path).run()
    cols = {r[1] for r in sqlite3.connect(path).execute("PRAGMA table_info(jobs)")}
    assert "last_run_at" in cols
    assert "created_at" in cols, "both exist — which is why the wrong one was silent"
