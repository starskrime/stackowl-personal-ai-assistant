"""Migration 0053 — durable-delegation link columns + index (D1 §4).

Verifies the durable `tasks` table gains the parent-link columns that connect a
delegated child durable task to its parent, the single-owner execution lease and
timeout tombstone, plus the child-lookup index. Mirrors the 0045 fixture style
(raw sqlite3 + PRAGMA introspection) and asserts the version gate makes the
migration idempotent on re-run.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from stackowl.db.migrations.runner import MigrationRunner

_LINK_COLUMNS = {
    "parent_task_id",
    "parent_owl",
    "delegate_key",
    "lease_owner",
    "superseded",
}


def _migrate(tmp_path: Path) -> Path:
    db_path = tmp_path / "d1.db"
    MigrationRunner(db_path=db_path).run()
    return db_path


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}  # noqa: S608


def test_0053_adds_link_columns(tmp_path: Path) -> None:
    db_path = _migrate(tmp_path)
    conn = sqlite3.connect(db_path)
    try:
        cols = _columns(conn, "tasks")
        assert cols >= _LINK_COLUMNS, f"missing link columns: {_LINK_COLUMNS - cols}"
    finally:
        conn.close()


def test_0053_superseded_defaults_zero(tmp_path: Path) -> None:
    db_path = _migrate(tmp_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO tasks (task_id, goal, status, created_at, updated_at) "
            "VALUES ('t1', 'g', 'pending', '2026-01-01', '2026-01-01')"
        )
        conn.commit()
        superseded = conn.execute(
            "SELECT superseded FROM tasks WHERE task_id = 't1'"
        ).fetchone()[0]
        assert superseded == 0
    finally:
        conn.close()


def test_0053_index_exists(tmp_path: Path) -> None:
    db_path = _migrate(tmp_path)
    conn = sqlite3.connect(db_path)
    try:
        names = {r[1] for r in conn.execute("PRAGMA index_list(tasks)")}
        assert "idx_tasks_parent" in names, f"missing idx_tasks_parent in {names}"
    finally:
        conn.close()


def test_0053_idempotent_rerun(tmp_path: Path) -> None:
    db_path = tmp_path / "d1.db"
    MigrationRunner(db_path=db_path).run()
    results = MigrationRunner(db_path=db_path).run()
    applied = [r for r in results if r.action == "applied"]
    assert applied == [], f"re-run applied migrations: {applied}"
    rec = next(r for r in results if r.version == "0053")
    assert rec.action == "skipped"
