"""Migration 0075 — re-arm pre-F-60 zombie recurring jobs (FR-11/12 item 3).

The migration's UPDATE only ever touches rows already ``status='failed'`` at
apply time, so the initial full-schema ``MigrationRunner.run()`` (empty jobs
table) is a no-op for it. To exercise the real behavior we seed ``failed`` rows
directly (simulating a DB carrying real pre-F-60 zombie state) and then execute
the migration's own SQL text a second (and third) time -- proving both that it
re-arms exactly the zombie row and that it is idempotent on repeat application.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from stackowl.db.migrations.runner import MigrationRunner

_MIGRATION_SQL = (
    Path(__file__).parents[2]
    / "src/stackowl/db/migrations/0075_rearm_zombie_failed_jobs.sql"
).read_text()


def _migrate(tmp_path: Path) -> Path:
    db_path = tmp_path / "d.db"
    MigrationRunner(db_path=db_path).run()
    return db_path


def _insert_job(
    conn: sqlite3.Connection,
    job_id: str,
    *,
    params: dict[str, object],
    enabled: int = 1,
) -> None:
    conn.execute(
        "INSERT INTO jobs (job_id, handler_name, schedule, idempotency_key, "
        "next_run_at, status, retry_count, created_at, enabled, params) "
        "VALUES (?, 'h', 'daily@09:00', ?, '2026-01-01T00:00:00', 'failed', 3, "
        "'2026-01-01T00:00:00', ?, ?)",
        (job_id, f"idem-{job_id}", enabled, json.dumps(params)),
    )


def _insert_audit(conn: sqlite3.Connection, job_id: str, event_type: str) -> None:
    conn.execute(
        "INSERT INTO audit_log (event_type, actor, target, timestamp, details) "
        "VALUES (?, 'scheduler', ?, ?, '{}')",
        (event_type, job_id, time.time()),
    )


def _row(conn: sqlite3.Connection, job_id: str) -> sqlite3.Row:
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT status, retry_count, retry_at FROM jobs WHERE job_id = ?", (job_id,)
    ).fetchone()
    assert row is not None
    return row  # type: ignore[no-any-return]


def test_0075_rearms_zombie_recurring_job_with_no_audit_trail(tmp_path: Path) -> None:
    db_path = _migrate(tmp_path)
    conn = sqlite3.connect(db_path)
    try:
        _insert_job(conn, "zombie1", params={})
        conn.execute("UPDATE jobs SET retry_at = '2026-01-01T00:05:00' WHERE job_id = 'zombie1'")
        conn.commit()

        conn.executescript(_MIGRATION_SQL)
        conn.commit()

        row = _row(conn, "zombie1")
        assert row["status"] == "pending"
        assert row["retry_count"] == 0
        assert row["retry_at"] is None
    finally:
        conn.close()


def test_0075_leaves_circuit_broken_owl_job_untouched(tmp_path: Path) -> None:
    db_path = _migrate(tmp_path)
    conn = sqlite3.connect(db_path)
    try:
        # S11c circuit-broken jobs are also disabled by _mark_failed, but the
        # audit-row check is the primary signal under test here.
        _insert_job(conn, "owlbroken", params={"source": "owl_lifecycle"}, enabled=0)
        _insert_audit(conn, "owlbroken", "owl_job_circuit_broken")
        conn.commit()

        conn.executescript(_MIGRATION_SQL)
        conn.commit()

        row = _row(conn, "owlbroken")
        assert row["status"] == "failed"
    finally:
        conn.close()


def test_0075_leaves_correctly_terminal_one_shot_untouched(tmp_path: Path) -> None:
    db_path = _migrate(tmp_path)
    conn = sqlite3.connect(db_path)
    try:
        _insert_job(conn, "oneshot1", params={"run_once": True})
        _insert_audit(conn, "oneshot1", "job_failed_terminal")
        conn.commit()

        conn.executescript(_MIGRATION_SQL)
        conn.commit()

        row = _row(conn, "oneshot1")
        assert row["status"] == "failed"
    finally:
        conn.close()


def test_0075_idempotent_on_repeat_application(tmp_path: Path) -> None:
    db_path = _migrate(tmp_path)
    conn = sqlite3.connect(db_path)
    try:
        _insert_job(conn, "zombie2", params={})
        conn.commit()

        conn.executescript(_MIGRATION_SQL)
        conn.commit()
        first = dict(_row(conn, "zombie2"))
        assert first["status"] == "pending"

        # Second application: the row is now 'pending', so the WHERE clause
        # (status='failed') no longer matches it -- no-op.
        conn.executescript(_MIGRATION_SQL)
        conn.commit()
        second = dict(_row(conn, "zombie2"))
        assert second == first
    finally:
        conn.close()
