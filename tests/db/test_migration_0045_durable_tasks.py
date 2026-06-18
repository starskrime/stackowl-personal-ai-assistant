"""Migration 0045 — durable-task primitive tables (Stage 1 Pass 3a).

Verifies both NEW tables are created owner-scoped from birth with the expected
columns and indexes, that owner_id defaults to the single-user principal, and
that the migration is a no-op on re-run.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from stackowl.db.migrations.runner import MigrationRunner
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID

_EXPECTED_INDEXES = {
    "tasks": {"idx_tasks_owner", "idx_tasks_status"},
    "side_effect_ledger": {"idx_sel_task", "idx_sel_owner"},
}


def _migrate(tmp_path: Path) -> Path:
    db_path = tmp_path / "m.db"
    MigrationRunner(db_path=db_path).run()
    return db_path


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}  # noqa: S608


def test_both_tables_created_with_owner_id(tmp_path: Path) -> None:
    db_path = _migrate(tmp_path)
    conn = sqlite3.connect(db_path)
    try:
        task_cols = _columns(conn, "tasks")
        assert {
            "task_id", "owner_id", "goal", "status", "current_step",
            "thread_id", "result", "created_at", "updated_at",
        } <= task_cols
        ledger_cols = _columns(conn, "side_effect_ledger")
        assert {
            "idempotency_key", "task_id", "owner_id", "step_index",
            "tool_name", "status", "result_blob", "created_at",
        } <= ledger_cols
    finally:
        conn.close()


def test_owner_id_defaults_to_default_principal(tmp_path: Path) -> None:
    db_path = _migrate(tmp_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO tasks (task_id, goal, status, created_at, updated_at) "
            "VALUES ('t1', 'g', 'pending', '2026-01-01', '2026-01-01')"
        )
        conn.execute(
            "INSERT INTO side_effect_ledger "
            "(idempotency_key, task_id, step_index, tool_name, status, created_at) "
            "VALUES ('k1', 't1', 0, 'tool', 'intent', '2026-01-01')"
        )
        conn.commit()
        task_owner = conn.execute(
            "SELECT owner_id FROM tasks WHERE task_id = 't1'"
        ).fetchone()[0]
        ledger_owner = conn.execute(
            "SELECT owner_id FROM side_effect_ledger WHERE idempotency_key = 'k1'"
        ).fetchone()[0]
        assert task_owner == DEFAULT_PRINCIPAL_ID
        assert ledger_owner == DEFAULT_PRINCIPAL_ID
    finally:
        conn.close()


def test_indexes_created(tmp_path: Path) -> None:
    db_path = _migrate(tmp_path)
    conn = sqlite3.connect(db_path)
    try:
        for table, expected in _EXPECTED_INDEXES.items():
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = ?",
                (table,),
            ).fetchall()
            names = {r[0] for r in rows}
            assert expected <= names, f"{table}: missing indexes {expected - names}"
    finally:
        conn.close()


def test_migration_0045_rerun_is_noop(tmp_path: Path) -> None:
    db_path = tmp_path / "m.db"
    MigrationRunner(db_path=db_path).run()
    results = MigrationRunner(db_path=db_path).run()
    applied = [r for r in results if r.action == "applied"]
    assert applied == [], f"re-run applied migrations: {applied}"
    rec = next(r for r in results if r.version == "0045")
    assert rec.action == "skipped"
