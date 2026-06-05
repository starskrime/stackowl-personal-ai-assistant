"""Migration 0047 — owl_name/channel recovery-context columns added to tasks (B4).

Verifies:
* The ``owl_name`` and ``channel`` columns are present after migration.
* Both are nullable (a task row can be inserted without supplying them).
* Pre-existing (legacy) task rows are unaffected — both default to NULL.
* The migration is idempotent (re-run is a no-op, recorded as "skipped").
* Every column that existed in 0045/0046 is preserved.
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


def test_recovery_context_columns_exist(tmp_path: Path) -> None:
    db_path = _migrate(tmp_path)
    conn = sqlite3.connect(db_path)
    try:
        cols = _columns(conn, "tasks")
        assert {"owl_name", "channel"} <= cols, (
            f"owl_name/channel columns missing from tasks; found: {cols}"
        )
    finally:
        conn.close()


def test_recovery_context_columns_are_nullable(tmp_path: Path) -> None:
    """A task row can be inserted without owl_name/channel — both NULL."""
    db_path = _migrate(tmp_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO tasks (task_id, owner_id, goal, status, created_at, updated_at) "
            "VALUES ('t-ctx-test', 'principal-default', 'test goal', 'pending', "
            "'2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"
        )
        conn.commit()
        row = conn.execute(
            "SELECT owl_name, channel FROM tasks WHERE task_id = 't-ctx-test'"
        ).fetchone()
        assert row is not None
        assert row[0] is None, f"Expected NULL owl_name, got: {row[0]!r}"
        assert row[1] is None, f"Expected NULL channel, got: {row[1]!r}"
    finally:
        conn.close()


def test_legacy_rows_unaffected(tmp_path: Path) -> None:
    """A row inserted before the new columns has NULL owl_name/channel — data intact."""
    db_path = _migrate(tmp_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO tasks "
            "(task_id, owner_id, goal, status, current_step, created_at, updated_at) "
            "VALUES ('legacy', 'principal-default', 'original goal', "
            "'running', 2, '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"
        )
        conn.commit()
        row = conn.execute(
            "SELECT goal, status, current_step, owl_name, channel "
            "FROM tasks WHERE task_id = 'legacy'"
        ).fetchone()
        assert row is not None
        goal, status, current_step, owl_name, channel = row
        assert goal == "original goal"
        assert status == "running"
        assert current_step == 2
        assert owl_name is None
        assert channel is None
    finally:
        conn.close()


def test_existing_tasks_columns_preserved(tmp_path: Path) -> None:
    """0047 must NOT remove or rename any column that existed in 0045/0046."""
    db_path = _migrate(tmp_path)
    conn = sqlite3.connect(db_path)
    try:
        cols = _columns(conn, "tasks")
        required = {
            "task_id", "owner_id", "goal", "status", "current_step",
            "thread_id", "result", "checkpoint_blob", "owl_name", "channel",
            "created_at", "updated_at",
        }
        missing = required - cols
        assert not missing, f"tasks table missing expected columns: {missing}"
    finally:
        conn.close()


def test_migration_0047_rerun_is_noop(tmp_path: Path) -> None:
    db_path = tmp_path / "m.db"
    MigrationRunner(db_path=db_path).run()
    results = MigrationRunner(db_path=db_path).run()
    applied = [r for r in results if r.action == "applied"]
    assert applied == [], f"re-run applied migrations: {applied}"
    rec = next((r for r in results if r.version == "0047"), None)
    assert rec is not None, "migration 0047 result not found in re-run results"
    assert rec.action == "skipped"
