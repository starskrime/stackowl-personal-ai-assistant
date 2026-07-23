"""Migration 0090 — retry_lineage_id / retry_event_count columns on
task_outcomes (Workstream B, Phase 5).

Follows test_migration_0083.py's fixture shape: MigrationRunner + raw
sqlite3, not a DbPool (the runner is the migration-apply entrypoint used by
``stackowl db migrate``).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from stackowl.db.migrations.runner import MigrationRunner


def _migrate(tmp_path: Path) -> Path:
    db_path = tmp_path / "d.db"
    MigrationRunner(db_path=db_path).run()
    return db_path


def test_0090_columns_exist(tmp_path: Path) -> None:
    db_path = _migrate(tmp_path)
    conn = sqlite3.connect(db_path)
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(task_outcomes)")}
        assert "retry_lineage_id" in columns
        assert "retry_event_count" in columns
    finally:
        conn.close()


def test_0090_runner_skips_already_applied_migration(tmp_path: Path) -> None:
    db_path = _migrate(tmp_path)
    results = MigrationRunner(db_path=db_path).run()
    result_0090 = next((r for r in results if r.version == "0090"), None)
    assert result_0090 is not None, f"no 0090 result in {[r.version for r in results]}"
    assert result_0090.action == "skipped"


def test_0090_retry_event_count_defaults_zero_retry_lineage_id_defaults_null(
    tmp_path: Path,
) -> None:
    db_path = _migrate(tmp_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO task_outcomes
                (trace_id, session_id, owl_name, channel, success, latency_ms, captured_at)
            VALUES
                ('trace-1', 'session-1', 'owl-1', 'telegram', 1, 12.5, 0.0)
            """
        )
        conn.execute(
            """
            INSERT INTO task_outcomes
                (trace_id, session_id, owl_name, channel, success, latency_ms, captured_at,
                 retry_lineage_id, retry_event_count)
            VALUES
                ('trace-2', 'session-2', 'owl-1', 'telegram', 1, 12.5, 0.0,
                 'retry-row-7', 3)
            """
        )
        rows = conn.execute(
            "SELECT trace_id, retry_lineage_id, retry_event_count "
            "FROM task_outcomes ORDER BY trace_id"
        ).fetchall()
        assert rows == [
            ("trace-1", None, 0),
            ("trace-2", "retry-row-7", 3),
        ]
    finally:
        conn.close()
