"""Migration 0151 — the COMMAND task kind (Story 4.3, AD-26).

Verifies the durable ``tasks`` table gains the 7 COMMAND-task columns
(``kind`` defaulted, the rest nullable), the partial unique index on
``command_id``, and the new ``command_receipts`` idempotency-guard table.
Mirrors 0053's own fixture style (raw sqlite3 + PRAGMA introspection).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from stackowl.db.migrations.runner import MigrationRunner

_COMMAND_COLUMNS = {
    "kind", "command_type", "command_payload", "command_id",
    "requester_kind", "nonce", "utterance_id",
}


def _migrate(tmp_path: Path) -> Path:
    db_path = tmp_path / "d151.db"
    MigrationRunner(db_path=db_path).run()
    return db_path


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}  # noqa: S608


def test_0151_adds_command_task_columns(tmp_path: Path) -> None:
    db_path = _migrate(tmp_path)
    conn = sqlite3.connect(db_path)
    try:
        cols = _columns(conn, "tasks")
        assert cols >= _COMMAND_COLUMNS, f"missing columns: {_COMMAND_COLUMNS - cols}"
    finally:
        conn.close()


def test_0151_kind_defaults_to_goal_for_a_legacy_style_insert(tmp_path: Path) -> None:
    """A caller that never mentions `kind` (every existing goal-task INSERT)
    must still read back 'goal' — zero behavior change."""
    db_path = _migrate(tmp_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO tasks (task_id, goal, status, created_at, updated_at) "
            "VALUES ('t1', 'g', 'pending', '2026-01-01', '2026-01-01')"
        )
        conn.commit()
        kind = conn.execute("SELECT kind FROM tasks WHERE task_id = 't1'").fetchone()[0]
        assert kind == "goal"
    finally:
        conn.close()


def test_0151_command_id_is_uniquely_constrained_only_when_present(tmp_path: Path) -> None:
    db_path = _migrate(tmp_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO tasks (task_id, goal, status, kind, command_id, "
            "created_at, updated_at) VALUES "
            "('t1', 'g', 'pending', 'command', 'c1', '2026-01-01', '2026-01-01')"
        )
        conn.commit()
        try:
            conn.execute(
                "INSERT INTO tasks (task_id, goal, status, kind, command_id, "
                "created_at, updated_at) VALUES "
                "('t2', 'g', 'pending', 'command', 'c1', '2026-01-01', '2026-01-01')"
            )
            conn.commit()
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError("a duplicate command_id must be rejected")
        # NULL command_id never collides — many goal tasks coexist fine.
        conn.execute(
            "INSERT INTO tasks (task_id, goal, status, created_at, updated_at) "
            "VALUES ('t3', 'g', 'pending', '2026-01-01', '2026-01-01')"
        )
        conn.execute(
            "INSERT INTO tasks (task_id, goal, status, created_at, updated_at) "
            "VALUES ('t4', 'g', 'pending', '2026-01-01', '2026-01-01')"
        )
        conn.commit()
        count = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE command_id IS NULL"
        ).fetchone()[0]
        assert count == 2
    finally:
        conn.close()


def test_0151_command_receipts_table_exists(tmp_path: Path) -> None:
    db_path = _migrate(tmp_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO command_receipts (command_id, command_type, executed_at) "
            "VALUES ('c1', 'scheduling.pause_job', '2026-01-01')"
        )
        conn.commit()
        row = conn.execute(
            "SELECT command_type FROM command_receipts WHERE command_id = 'c1'"
        ).fetchone()
        assert row == ("scheduling.pause_job",)
        # INSERT OR IGNORE — the shape record_command_execution relies on —
        # a second row for the same command_id is a documented no-op, not
        # an error.
        conn.execute(
            "INSERT OR IGNORE INTO command_receipts (command_id, command_type, "
            "executed_at) VALUES ('c1', 'scheduling.resume_job', '2026-01-02')"
        )
        conn.commit()
        count = conn.execute(
            "SELECT COUNT(*) FROM command_receipts WHERE command_id = 'c1'"
        ).fetchone()[0]
        assert count == 1
    finally:
        conn.close()


def test_0151_idempotent_rerun(tmp_path: Path) -> None:
    db_path = tmp_path / "d151.db"
    MigrationRunner(db_path=db_path).run()
    results = MigrationRunner(db_path=db_path).run()
    applied = [r for r in results if r.action == "applied"]
    assert applied == [], f"re-run applied migrations: {applied}"
    rec = next(r for r in results if r.version == "0151")
    assert rec.action == "skipped"
