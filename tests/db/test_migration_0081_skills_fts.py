"""Migration 0081 — skills_fts FTS5 keyword index (Story LAT.2, Phase 2).

MigrationRunner records applied versions and skips a version already applied,
so to exercise the migration's OWN idempotency (CREATE VIRTUAL TABLE IF NOT
EXISTS + the backfill INSERT) we execute the migration's raw SQL text a
second time directly, mirroring test_migration_0075_rearm_zombie_jobs.py's
pattern.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from stackowl.db.migrations.runner import MigrationRunner

_MIGRATION_SQL = (
    Path(__file__).parents[2] / "src/stackowl/db/migrations/0081_skills_fts.sql"
).read_text()


def _migrate(tmp_path: Path) -> Path:
    db_path = tmp_path / "d.db"
    MigrationRunner(db_path=db_path).run()
    return db_path


def _insert_skill(conn: sqlite3.Connection, name: str, *, enabled: int = 1) -> int:
    now = time.time()
    conn.execute(
        "INSERT INTO skills (name, source, path, description, when_to_use, "
        "version, enabled, n_executions, loaded_at, updated_at) "
        "VALUES (?, 'user', '/p', 'a description', 'when to use it', '0.0.0', ?, 0, ?, ?)",
        (name, enabled, now, now),
    )
    row = conn.execute("SELECT skill_id FROM skills WHERE name = ?", (name,)).fetchone()
    assert row is not None
    return int(row[0])


def test_0081_creates_fts_table_and_backfills_enabled_skills(tmp_path: Path) -> None:
    """The initial full-schema run (empty skills table) must apply cleanly, and
    a skill inserted afterward is reachable once the migration's own SQL text
    (CREATE + backfill) is re-run against it."""
    db_path = _migrate(tmp_path)
    conn = sqlite3.connect(db_path)
    try:
        skill_id = _insert_skill(conn, "alpha")
        conn.commit()

        conn.executescript(_MIGRATION_SQL)
        conn.commit()

        row = conn.execute(
            "SELECT rowid FROM skills_fts WHERE rowid = ?", (skill_id,)
        ).fetchone()
        assert row is not None
    finally:
        conn.close()


def test_0081_idempotent_on_repeat_application(tmp_path: Path) -> None:
    """Running the migration's SQL text twice must not error and must not
    duplicate an FTS row for a skill_id already indexed."""
    db_path = _migrate(tmp_path)
    conn = sqlite3.connect(db_path)
    try:
        skill_id = _insert_skill(conn, "beta")
        conn.commit()

        conn.executescript(_MIGRATION_SQL)
        conn.commit()
        conn.executescript(_MIGRATION_SQL)  # second application — must not raise
        conn.commit()

        count = conn.execute(
            "SELECT COUNT(*) FROM skills_fts WHERE rowid = ?", (skill_id,)
        ).fetchone()[0]
        assert count == 1
    finally:
        conn.close()


def test_0081_skips_disabled_skills_in_backfill(tmp_path: Path) -> None:
    db_path = _migrate(tmp_path)
    conn = sqlite3.connect(db_path)
    try:
        skill_id = _insert_skill(conn, "gamma-disabled", enabled=0)
        conn.commit()

        conn.executescript(_MIGRATION_SQL)
        conn.commit()

        row = conn.execute(
            "SELECT rowid FROM skills_fts WHERE rowid = ?", (skill_id,)
        ).fetchone()
        assert row is None
    finally:
        conn.close()
