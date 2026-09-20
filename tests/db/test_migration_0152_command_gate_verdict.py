"""Migration 0152 — the action-policy gate's durable resume marker (Story
4.4, AD-27/AD-28).

Mirrors ``test_migration_0151_command_task_fields.py``'s own style: raw
sqlite3 + PRAGMA introspection, run against the real ``MigrationRunner``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from stackowl.db.migrations.runner import MigrationRunner


def _migrate(tmp_path: Path) -> Path:
    db_path = tmp_path / "d152.db"
    MigrationRunner(db_path=db_path).run()
    return db_path


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}  # noqa: S608


def test_0152_adds_gate_verdict_column(tmp_path: Path) -> None:
    db_path = _migrate(tmp_path)
    conn = sqlite3.connect(db_path)
    try:
        cols = _columns(conn, "tasks")
        assert "gate_verdict" in cols
    finally:
        conn.close()


def test_0152_gate_verdict_defaults_to_null_for_a_legacy_style_insert(
    tmp_path: Path,
) -> None:
    """A caller that never mentions `gate_verdict` (every existing task
    INSERT) must still read back NULL — zero behavior change."""
    db_path = _migrate(tmp_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO tasks (task_id, goal, status, created_at, updated_at) "
            "VALUES ('t1', 'g', 'pending', '2026-01-01', '2026-01-01')"
        )
        conn.commit()
        verdict = conn.execute(
            "SELECT gate_verdict FROM tasks WHERE task_id = 't1'"
        ).fetchone()[0]
        assert verdict is None
    finally:
        conn.close()


def test_0152_gate_verdict_round_trips_an_approved_write(tmp_path: Path) -> None:
    db_path = _migrate(tmp_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO tasks (task_id, goal, status, kind, gate_verdict, "
            "created_at, updated_at) VALUES "
            "('t1', 'g', 'pending', 'command', 'approved', '2026-01-01', '2026-01-01')"
        )
        conn.commit()
        verdict = conn.execute(
            "SELECT gate_verdict FROM tasks WHERE task_id = 't1'"
        ).fetchone()[0]
        assert verdict == "approved"
    finally:
        conn.close()


def test_0152_idempotent_rerun(tmp_path: Path) -> None:
    db_path = tmp_path / "d152.db"
    MigrationRunner(db_path=db_path).run()
    results = MigrationRunner(db_path=db_path).run()
    applied = [r for r in results if r.action == "applied"]
    assert applied == [], f"re-run applied migrations: {applied}"
    rec = next(r for r in results if r.version == "0152")
    assert rec.action == "skipped"
