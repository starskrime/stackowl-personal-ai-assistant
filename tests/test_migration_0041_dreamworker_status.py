"""Migration 0041 — dreamworker_runs status/error/stuck_eligible columns.

Verifies the failure-tracker columns exist after migrate, and that a re-run of
the runner is a clean no-op (once-only via schema_migrations).
"""

from __future__ import annotations

from pathlib import Path

from stackowl.db.migrations.runner import MigrationRunner


def _columns(db_path: Path) -> set[str]:
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("PRAGMA table_info(dreamworker_runs)").fetchall()
        return {r[1] for r in rows}
    finally:
        conn.close()


def test_status_columns_present_after_migrate(tmp_path: Path) -> None:
    db_path = tmp_path / "m.db"
    MigrationRunner(db_path=db_path).run()
    cols = _columns(db_path)
    assert {"status", "error", "stuck_eligible"} <= cols


def test_migration_rerun_is_noop(tmp_path: Path) -> None:
    db_path = tmp_path / "m.db"
    MigrationRunner(db_path=db_path).run()
    # Second run: 0041 must be 'skipped', not re-applied (idempotent/once-only).
    results = MigrationRunner(db_path=db_path).run()
    applied = [r for r in results if r.action == "applied"]
    assert applied == [], f"re-run applied migrations unexpectedly: {applied}"
    rec = next(r for r in results if r.version == "0041")
    assert rec.action == "skipped"
