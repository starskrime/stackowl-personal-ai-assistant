"""Migration 0054 — durable delivery target columns on ``jobs``.

A cron-born job has no durable recipient (C1 root cause): it must carry the
channels + native addresses it should deliver to, persisted on the row so a
fresh scheduler process (no session, no TraceContext) can resolve a recipient
without riding telegram's shared ``_last_chat_id``. This verifies the two
nullable columns exist after migrate and that a re-run is a clean no-op.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from stackowl.db.migrations.runner import MigrationRunner


def _columns(db_path: Path, table: str) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return {r[1] for r in rows}
    finally:
        conn.close()


def test_target_columns_present_after_migrate(tmp_path: Path) -> None:
    db_path = tmp_path / "m.db"
    MigrationRunner(db_path=db_path).run()
    cols = _columns(db_path, "jobs")
    assert {"target_channels", "target_addresses"} <= cols


def test_target_columns_are_nullable(tmp_path: Path) -> None:
    """An existing customer row (no targets) must round-trip — columns nullable."""
    db_path = tmp_path / "m.db"
    MigrationRunner(db_path=db_path).run()
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO jobs (job_id, handler_name, schedule, idempotency_key, "
            "next_run_at, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "j1",
                "morning_brief",
                "daily@08:00",
                "k1",
                "2026-01-01T08:00:00+00:00",
                "pending",
                "2026-01-01T00:00:00+00:00",
            ),
        )
        row = conn.execute(
            "SELECT target_channels, target_addresses FROM jobs WHERE job_id = 'j1'"
        ).fetchone()
        assert row == (None, None)
    finally:
        conn.close()


def test_migration_rerun_is_noop(tmp_path: Path) -> None:
    db_path = tmp_path / "m.db"
    MigrationRunner(db_path=db_path).run()
    results = MigrationRunner(db_path=db_path).run()
    applied = [r for r in results if r.action == "applied"]
    assert applied == [], f"re-run applied migrations unexpectedly: {applied}"
    rec = next(r for r in results if r.version == "0054")
    assert rec.action == "skipped"
