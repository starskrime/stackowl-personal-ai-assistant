"""Migration 0120 — the composite index the owl-ratings health probe needs.

The probe was reporting a healthy subsystem as DOWN because it could not finish
inside its own 5,000ms budget: one query per owl, serially, each one landing on
`idx_task_outcomes_owner` — and this deployment has exactly ONE owner, so that
"index scan" selected the whole table. Measured on the live database with the box
idle: 4,464ms warm, 27,092ms cold, against a 5,000ms timeout.

The tests below pin the two things that make the fix real, because either could
regress in silence:

* the index EXISTS and is idempotent (the 0090 fixture shape);
* SQLite actually CHOOSES it. An index the planner ignores is the same defect
  with an extra B-tree — and the planner ignoring it is exactly what happened to
  `idx_task_outcomes_owl` before this.

Follows test_migration_0090.py's fixture shape: MigrationRunner + raw sqlite3.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from stackowl.db.migrations.runner import MigrationRunner

_INDEX = "idx_task_outcomes_owner_owl_captured"

#: The probe's query, verbatim from
#: ``TaskOutcomeStore.count_approach_ratings_for_owl``. Copied rather than
#: imported because the store is async and needs a pool; the assertion that
#: matters is about the PLAN, and a plan needs the literal SQL.
_PROBE_QUERY = """
    SELECT approach_rating, COUNT(*) AS n
      FROM task_outcomes
     WHERE owner_id = ?
       AND owl_name = ?
       AND captured_at >= ?
       AND approach_rating IN ('positive', 'negative')
     GROUP BY approach_rating
"""


def _migrate(tmp_path: Path) -> Path:
    db_path = tmp_path / "d.db"
    MigrationRunner(db_path=db_path).run()
    return db_path


def test_0120_index_exists(tmp_path: Path) -> None:
    db_path = _migrate(tmp_path)
    conn = sqlite3.connect(db_path)
    try:
        names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='index' AND tbl_name='task_outcomes'"
            )
        }
        assert _INDEX in names, f"missing {_INDEX}; have {sorted(names)}"
    finally:
        conn.close()


def test_0120_columns_are_equality_first_then_range(tmp_path: Path) -> None:
    """Order is behaviour, not style. Equality columns must precede the range
    column or SQLite can only FILTER on captured_at instead of seeking with it."""
    db_path = _migrate(tmp_path)
    conn = sqlite3.connect(db_path)
    try:
        cols = [row[2] for row in conn.execute(f"PRAGMA index_info({_INDEX})")]
        assert cols == ["owner_id", "owl_name", "captured_at"], cols
    finally:
        conn.close()


def test_0120_the_planner_actually_uses_it(tmp_path: Path) -> None:
    """THE ASSERTION THAT MATTERS. An index the planner declines to use is the
    original defect plus an extra B-tree to maintain — and that is not
    hypothetical here: `idx_task_outcomes_owl` already existed and SQLite chose
    `idx_task_outcomes_owner` over it, which on a single-owner deployment means a
    full table scan."""
    db_path = _migrate(tmp_path)
    conn = sqlite3.connect(db_path)
    try:
        plan = " ".join(
            str(row[3])
            for row in conn.execute(
                "EXPLAIN QUERY PLAN " + _PROBE_QUERY,
                ("principal-default", "secretary", 0.0),
            )
        )
        assert _INDEX in plan, f"planner did not choose {_INDEX}: {plan}"
        assert "captured_at" in plan, (
            "captured_at is being filtered rather than seeked — the range column "
            f"is not participating in the index lookup: {plan}"
        )
    finally:
        conn.close()


def test_0120_runner_skips_already_applied_migration(tmp_path: Path) -> None:
    db_path = _migrate(tmp_path)
    results = MigrationRunner(db_path=db_path).run()
    result = next((r for r in results if r.version == "0120"), None)
    assert result is not None, f"no 0120 result in {[r.version for r in results]}"
    assert result.action == "skipped"
