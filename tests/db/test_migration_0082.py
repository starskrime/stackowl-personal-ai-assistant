"""Migration 0082 — retry_queue table (Story 1.1, failure-retry-loop).

MigrationRunner records applied versions and skips a version already applied,
so to exercise the migration's OWN idempotency (CREATE TABLE IF NOT EXISTS +
CREATE INDEX IF NOT EXISTS) we execute the migration's raw SQL text a second
time directly, mirroring test_migration_0081_skills_fts.py's pattern.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from stackowl.db.migrations.runner import MigrationRunner

_MIGRATION_SQL = (
    Path(__file__).parents[2] / "src/stackowl/db/migrations/0082_retry_queue.sql"
).read_text()

_EXPECTED_COLUMNS = {
    "id",
    "trace_id",
    "session_id",
    "goal",
    "banned_capabilities",
    "attempt_count",
    "status",
    "next_retry_at",
    "last_error",
    "channel",
    "channel_chat_id",
    "channel_message_id",
    "owner_id",
    "created_at",
    "updated_at",
}

# (name, type, notnull, dflt_value) — the columns whose constraints/defaults
# matter for correctness, per PRAGMA table_info. Not exhaustive (nullable
# free-text columns like last_error are covered by _EXPECTED_COLUMNS alone).
_EXPECTED_COLUMN_CONSTRAINTS = {
    ("id", "TEXT", 0, None),
    ("owner_id", "TEXT", 1, None),
    ("attempt_count", "INTEGER", 1, "0"),
    ("status", "TEXT", 1, None),
    ("channel", "TEXT", 1, "'telegram'"),
    ("banned_capabilities", "TEXT", 1, "'[]'"),
}

_EXPECTED_INDEXES = {
    "idx_retry_queue_status_due",
    "idx_retry_queue_session",
    "idx_retry_queue_trace",
}


def _migrate(tmp_path: Path) -> Path:
    db_path = tmp_path / "d.db"
    MigrationRunner(db_path=db_path).run()
    return db_path


def test_0082_creates_retry_queue_table_with_all_columns_and_indexes(tmp_path: Path) -> None:
    db_path = _migrate(tmp_path)
    conn = sqlite3.connect(db_path)
    try:
        table_row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'retry_queue'"
        ).fetchone()
        assert table_row is not None

        table_info = conn.execute("PRAGMA table_info(retry_queue)").fetchall()
        columns = {row[1] for row in table_info}
        assert columns == _EXPECTED_COLUMNS

        constraints = {(row[1], row[2], row[3], row[4]) for row in table_info}
        assert constraints >= _EXPECTED_COLUMN_CONSTRAINTS

        index_names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'retry_queue'"
            ).fetchall()
        }
        assert index_names >= _EXPECTED_INDEXES
    finally:
        conn.close()


def test_0082_runner_skips_already_applied_migration(tmp_path: Path) -> None:
    db_path = _migrate(tmp_path)
    results = MigrationRunner(db_path=db_path).run()
    result_0082 = next((r for r in results if r.version == "0082"), None)
    assert result_0082 is not None, f"no 0082 result in {[r.version for r in results]}"
    assert result_0082.action == "skipped"


def test_0082_insert_applies_defaults_and_enforces_status_check(tmp_path: Path) -> None:
    db_path = _migrate(tmp_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO retry_queue
                (id, trace_id, session_id, goal, status, next_retry_at,
                 owner_id, created_at, updated_at)
            VALUES
                ('id-1', 'trace-1', 'session-1', 'do the thing', 'pending',
                 '2026-07-12T00:00:00+00:00', 'owner-1',
                 '2026-07-12T00:00:00+00:00', '2026-07-12T00:00:00+00:00')
            """
        )
        row = conn.execute(
            "SELECT banned_capabilities, attempt_count, channel FROM retry_queue WHERE id = 'id-1'"
        ).fetchone()
        assert row == ("[]", 0, "telegram")

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO retry_queue
                    (id, trace_id, session_id, goal, status, next_retry_at,
                     owner_id, created_at, updated_at)
                VALUES
                    ('id-2', 'trace-2', 'session-2', 'do another thing', 'bogus-status',
                     '2026-07-12T00:00:00+00:00', 'owner-1',
                     '2026-07-12T00:00:00+00:00', '2026-07-12T00:00:00+00:00')
                """
            )
    finally:
        conn.close()


def test_0082_idempotent_on_raw_sql_reapplication(tmp_path: Path) -> None:
    """Running the migration's SQL text twice directly (bypassing the
    runner's version-skip) must not error and must not duplicate the table or
    any index in sqlite_master."""
    db_path = _migrate(tmp_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(_MIGRATION_SQL)
        conn.commit()
        conn.executescript(_MIGRATION_SQL)  # second application — must not raise
        conn.commit()

        table_count = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'retry_queue'"
        ).fetchone()[0]
        assert table_count == 1

        for index_name in _EXPECTED_INDEXES:
            index_count = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type = 'index' AND name = ?",
                (index_name,),
            ).fetchone()[0]
            assert index_count == 1
    finally:
        conn.close()
