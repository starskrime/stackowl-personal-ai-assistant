"""Migration 0153 -- standing authority (Story 4.6, AD-27).

Mirrors ``test_migration_0151_command_task_fields.py``/``test_migration_0152_
command_gate_verdict.py``'s own style: raw sqlite3 + PRAGMA introspection,
run against the real ``MigrationRunner``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from stackowl.db.migrations.runner import MigrationRunner

_STANDING_AUTHORITY_COLUMNS = {
    "id", "scope_kind", "scope_id", "command_type", "granted_by",
    "provenance", "granted_at", "revoked_at",
}


def _migrate(tmp_path: Path) -> Path:
    db_path = tmp_path / "d153.db"
    MigrationRunner(db_path=db_path).run()
    return db_path


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}  # noqa: S608


def test_0153_creates_the_standing_authority_table(tmp_path: Path) -> None:
    db_path = _migrate(tmp_path)
    conn = sqlite3.connect(db_path)
    try:
        cols = _columns(conn, "standing_authority")
        assert cols == _STANDING_AUTHORITY_COLUMNS
    finally:
        conn.close()


def test_0153_standing_authority_round_trips_a_row(tmp_path: Path) -> None:
    db_path = _migrate(tmp_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO standing_authority (id, scope_kind, scope_id, "
            "command_type, granted_by, provenance, granted_at, revoked_at) "
            "VALUES ('a1', 'job', 'job-1', 'delivery.send_message', 'owner', "
            "'granted', '2026-01-01T00:00:00+00:00', NULL)"
        )
        conn.commit()
        row = conn.execute(
            "SELECT scope_kind, scope_id, command_type, revoked_at FROM "
            "standing_authority WHERE id = 'a1'"
        ).fetchone()
        assert row == ("job", "job-1", "delivery.send_message", None)
    finally:
        conn.close()


def test_0153_active_grant_index_allows_two_revoked_rows_for_the_same_scope(
    tmp_path: Path,
) -> None:
    """The partial index is on `revoked_at IS NULL` -- it must never block
    two historical (already-revoked) rows for the same scope/command_type."""
    db_path = _migrate(tmp_path)
    conn = sqlite3.connect(db_path)
    try:
        for row_id in ("a1", "a2"):
            conn.execute(
                "INSERT INTO standing_authority (id, scope_kind, scope_id, "
                "command_type, granted_by, provenance, granted_at, revoked_at) "
                "VALUES (?, 'job', 'job-1', 'delivery.send_message', 'owner', "
                "'granted', '2026-01-01T00:00:00+00:00', '2026-01-02T00:00:00+00:00')",
                (row_id,),
            )
        conn.commit()
        count = conn.execute(
            "SELECT COUNT(*) FROM standing_authority WHERE scope_id = 'job-1'"
        ).fetchone()[0]
        assert count == 2
    finally:
        conn.close()


def test_0153_adds_tasks_authority_grant_id_column(tmp_path: Path) -> None:
    db_path = _migrate(tmp_path)
    conn = sqlite3.connect(db_path)
    try:
        cols = _columns(conn, "tasks")
        assert "authority_grant_id" in cols
    finally:
        conn.close()


def test_0153_authority_grant_id_defaults_to_null_for_a_legacy_style_insert(
    tmp_path: Path,
) -> None:
    db_path = _migrate(tmp_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO tasks (task_id, goal, status, created_at, updated_at) "
            "VALUES ('t1', 'g', 'pending', '2026-01-01', '2026-01-01')"
        )
        conn.commit()
        value = conn.execute(
            "SELECT authority_grant_id FROM tasks WHERE task_id = 't1'"
        ).fetchone()[0]
        assert value is None
    finally:
        conn.close()


def test_0153_adds_jobs_preauthorized_command_types_column(tmp_path: Path) -> None:
    db_path = _migrate(tmp_path)
    conn = sqlite3.connect(db_path)
    try:
        cols = _columns(conn, "jobs")
        assert "preauthorized_command_types" in cols
    finally:
        conn.close()


def test_0153_idempotent_rerun(tmp_path: Path) -> None:
    db_path = tmp_path / "d153.db"
    MigrationRunner(db_path=db_path).run()
    results = MigrationRunner(db_path=db_path).run()
    applied = [r for r in results if r.action == "applied"]
    assert applied == [], f"re-run applied migrations: {applied}"
    rec = next(r for r in results if r.version == "0153")
    assert rec.action == "skipped"
