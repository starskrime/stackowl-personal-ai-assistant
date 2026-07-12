"""Migration 0083 — approach_rating column on task_outcomes (approach-rating
buttons, Task 1).

Follows test_migration_0082.py's fixture shape: MigrationRunner + raw
sqlite3, not a DbPool (the repo has no such class — the runner is the
migration-apply entrypoint used by ``stackowl db migrate``).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from stackowl.db.migrations.runner import MigrationRunner


def _migrate(tmp_path: Path) -> Path:
    db_path = tmp_path / "d.db"
    MigrationRunner(db_path=db_path).run()
    return db_path


def test_0083_approach_rating_column_exists(tmp_path: Path) -> None:
    db_path = _migrate(tmp_path)
    conn = sqlite3.connect(db_path)
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(task_outcomes)")}
        assert "approach_rating" in columns
    finally:
        conn.close()


def test_0083_runner_skips_already_applied_migration(tmp_path: Path) -> None:
    db_path = _migrate(tmp_path)
    results = MigrationRunner(db_path=db_path).run()
    result_0083 = next((r for r in results if r.version == "0083"), None)
    assert result_0083 is not None, f"no 0083 result in {[r.version for r in results]}"
    assert result_0083.action == "skipped"


def test_0083_accepts_positive_negative_and_null_rejects_other_values(tmp_path: Path) -> None:
    db_path = _migrate(tmp_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO task_outcomes
                (trace_id, session_id, owl_name, channel, success, latency_ms, captured_at,
                 approach_rating)
            VALUES
                ('trace-1', 'session-1', 'owl-1', 'telegram', 1, 12.5, 0.0, 'positive')
            """
        )
        conn.execute(
            """
            INSERT INTO task_outcomes
                (trace_id, session_id, owl_name, channel, success, latency_ms, captured_at,
                 approach_rating)
            VALUES
                ('trace-2', 'session-2', 'owl-1', 'telegram', 1, 12.5, 0.0, 'negative')
            """
        )
        conn.execute(
            """
            INSERT INTO task_outcomes
                (trace_id, session_id, owl_name, channel, success, latency_ms, captured_at)
            VALUES
                ('trace-3', 'session-3', 'owl-1', 'telegram', 1, 12.5, 0.0)
            """
        )
        rows = conn.execute(
            "SELECT trace_id, approach_rating FROM task_outcomes ORDER BY trace_id"
        ).fetchall()
        assert rows == [
            ("trace-1", "positive"),
            ("trace-2", "negative"),
            ("trace-3", None),
        ]

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO task_outcomes
                    (trace_id, session_id, owl_name, channel, success, latency_ms, captured_at,
                     approach_rating)
                VALUES
                    ('trace-4', 'session-4', 'owl-1', 'telegram', 1, 12.5, 0.0, 'bogus')
                """
            )
    finally:
        conn.close()
