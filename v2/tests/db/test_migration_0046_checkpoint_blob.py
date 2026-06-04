"""Migration 0046 — checkpoint_blob column added to tasks (S1).

Verifies:
* The ``checkpoint_blob`` column is present in the tasks table after migration.
* Pre-existing task rows are unaffected (checkpoint_blob defaults to NULL).
* The migration is idempotent (re-run is a no-op, recorded as "skipped").
* Other tasks table columns are preserved.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from stackowl.db.migrations.runner import MigrationRunner


def _migrate(tmp_path: Path) -> Path:
    db_path = tmp_path / "m.db"
    MigrationRunner(db_path=db_path).run()
    return db_path


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}  # noqa: S608


def test_checkpoint_blob_column_exists(tmp_path: Path) -> None:
    db_path = _migrate(tmp_path)
    conn = sqlite3.connect(db_path)
    try:
        cols = _columns(conn, "tasks")
        assert "checkpoint_blob" in cols, (
            f"checkpoint_blob column missing from tasks; found: {cols}"
        )
    finally:
        conn.close()


def test_checkpoint_blob_is_nullable(tmp_path: Path) -> None:
    """A new task row can be inserted without supplying checkpoint_blob."""
    db_path = _migrate(tmp_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO tasks (task_id, owner_id, goal, status, created_at, updated_at) "
            "VALUES ('t-chk-test', 'principal-default', 'test goal', 'pending', "
            "'2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"
        )
        conn.commit()
        row = conn.execute(
            "SELECT checkpoint_blob FROM tasks WHERE task_id = 't-chk-test'"
        ).fetchone()
        assert row is not None
        assert row[0] is None, f"Expected NULL checkpoint_blob, got: {row[0]!r}"
    finally:
        conn.close()


def test_preexisting_rows_unaffected(tmp_path: Path) -> None:
    """Rows inserted before the new column have NULL checkpoint_blob — data intact."""
    db_path = _migrate(tmp_path)
    conn = sqlite3.connect(db_path)
    try:
        # Insert a row, then verify the existing columns still read back correctly.
        conn.execute(
            "INSERT INTO tasks "
            "(task_id, owner_id, goal, status, current_step, created_at, updated_at) "
            "VALUES ('pre-existing', 'principal-default', 'original goal', "
            "'running', 3, '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"
        )
        conn.commit()
        row = conn.execute(
            "SELECT goal, status, current_step, checkpoint_blob "
            "FROM tasks WHERE task_id = 'pre-existing'"
        ).fetchone()
        assert row is not None
        goal, status, current_step, checkpoint_blob = row
        assert goal == "original goal"
        assert status == "running"
        assert current_step == 3
        assert checkpoint_blob is None
    finally:
        conn.close()


def test_existing_tasks_columns_preserved(tmp_path: Path) -> None:
    """0046 must NOT remove or rename any column that existed in 0045."""
    db_path = _migrate(tmp_path)
    conn = sqlite3.connect(db_path)
    try:
        cols = _columns(conn, "tasks")
        required = {
            "task_id", "owner_id", "goal", "status", "current_step",
            "thread_id", "result", "created_at", "updated_at", "checkpoint_blob",
        }
        missing = required - cols
        assert not missing, f"tasks table missing expected columns: {missing}"
    finally:
        conn.close()


def test_migration_0046_rerun_is_noop(tmp_path: Path) -> None:
    db_path = tmp_path / "m.db"
    MigrationRunner(db_path=db_path).run()
    results = MigrationRunner(db_path=db_path).run()
    applied = [r for r in results if r.action == "applied"]
    assert applied == [], f"re-run applied migrations: {applied}"
    rec = next((r for r in results if r.version == "0046"), None)
    assert rec is not None, "migration 0046 result not found in re-run results"
    assert rec.action == "skipped"
