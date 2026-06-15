"""Migration 0055 — delivery-attempt ledger (exactly-once delivery, occurrence-scoped).

F103 delivery half: a crash between a successful send and the ``job_runs``
completion INSERT replays the handler. A ``dispatched``-state pre-record keyed by
(job_id, occurrence_key, channel) suppresses the re-send on replay WITHOUT
collapsing the existing occurrence_key dedup (the frozen-scheduler fix stays).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from stackowl.db.migrations.runner import MigrationRunner


def _columns(db_path: Path, table: str) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return {r[1] for r in rows}
    finally:
        conn.close()


def test_ledger_table_present_with_expected_columns(tmp_path: Path) -> None:
    db_path = tmp_path / "m.db"
    MigrationRunner(db_path=db_path).run()
    cols = _columns(db_path, "delivery_attempts")
    assert {"job_id", "occurrence_key", "channel", "state", "created_at", "updated_at"} <= cols


def test_ledger_key_is_occurrence_scoped_unique(tmp_path: Path) -> None:
    """Same (job_id, occurrence_key, channel) cannot be inserted twice."""
    db_path = tmp_path / "m.db"
    MigrationRunner(db_path=db_path).run()
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO delivery_attempts (job_id, occurrence_key, channel, state, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("j1", "k1@t1", "telegram", "dispatched", "t", "t"),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO delivery_attempts (job_id, occurrence_key, channel, state, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                ("j1", "k1@t1", "telegram", "dispatched", "t", "t"),
            )
    finally:
        conn.close()


def test_different_occurrence_is_a_new_row(tmp_path: Path) -> None:
    """A DIFFERENT occurrence_key (next scheduled instant) is NOT deduped away."""
    db_path = tmp_path / "m.db"
    MigrationRunner(db_path=db_path).run()
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO delivery_attempts (job_id, occurrence_key, channel, state, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("j1", "k1@t1", "telegram", "delivered", "t", "t"),
        )
        # next occurrence — must be allowed (recurring jobs fire again)
        conn.execute(
            "INSERT INTO delivery_attempts (job_id, occurrence_key, channel, state, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("j1", "k1@t2", "telegram", "dispatched", "t", "t"),
        )
        n = conn.execute(
            "SELECT COUNT(*) FROM delivery_attempts WHERE job_id = 'j1'"
        ).fetchone()[0]
        assert n == 2
    finally:
        conn.close()


def test_migration_rerun_is_noop(tmp_path: Path) -> None:
    db_path = tmp_path / "m.db"
    MigrationRunner(db_path=db_path).run()
    results = MigrationRunner(db_path=db_path).run()
    applied = [r for r in results if r.action == "applied"]
    assert applied == [], f"re-run applied migrations unexpectedly: {applied}"
    rec = next(r for r in results if r.version == "0055")
    assert rec.action == "skipped"
