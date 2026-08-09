"""skills_fts — the FTS5 keyword index, as it stands after every migration.

REPLACES test_migration_0081_skills_fts.py, which replayed 0081's raw SQL text a
second time to exercise "the migration's own idempotency". That pattern works
for a migration built from `CREATE ... IF NOT EXISTS`; it does not work here any
more, for two independent reasons:

  * 0110 rebuilt skills_fts WITHOUT the `summary` column (D09.3 slice 5), so
    replaying 0081's INSERT fails on a column the table no longer has.
  * 0110's own text cannot be replayed either: SQLite has no
    `ALTER TABLE ... DROP COLUMN IF EXISTS`.

Neither is a defect. This project's convention, stated in 0043, is that the
version guard in `schema_migrations` IS the idempotency mechanism — the runner
never re-applies an applied version. So the thing worth testing is the SCHEMA
THAT RESULTS and the behaviour that depends on it, not whether a particular
file's bytes can be run twice.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from stackowl.db.migrations.runner import MigrationRunner


def _migrated(tmp_path: Path) -> sqlite3.Connection:
    db_path = tmp_path / "d.db"
    MigrationRunner(db_path=db_path).run()
    return sqlite3.connect(db_path)


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


def test_the_fts_table_exists_after_migration(tmp_path: Path) -> None:
    conn = _migrated(tmp_path)
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='skills_fts'"
        ).fetchone()
        assert row is not None
    finally:
        conn.close()


def test_its_columns_are_the_post_0110_set(tmp_path: Path) -> None:
    """The whole point of 0110. An FTS index still describing a dropped column
    makes every keyword query a hard error, so this cannot be left to drift."""
    conn = _migrated(tmp_path)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(skills_fts)")}
        assert cols == {"name", "description", "when_to_use"}
    finally:
        conn.close()


def test_a_skill_is_reachable_by_keyword_once_synced(tmp_path: Path) -> None:
    """The behaviour the index exists for, exercised through the same INSERT the
    application performs — which is what actually has to keep working."""
    conn = _migrated(tmp_path)
    try:
        skill_id = _insert_skill(conn, "frobnicator")
        conn.execute(
            "INSERT INTO skills_fts (rowid, name, description, when_to_use) "
            "SELECT skill_id, name, COALESCE(description, ''), COALESCE(when_to_use, '') "
            "FROM skills WHERE skill_id = ?",
            (skill_id,),
        )
        conn.commit()

        row = conn.execute(
            "SELECT rowid FROM skills_fts WHERE skills_fts MATCH 'frobnicator'"
        ).fetchone()

        assert row is not None
        assert int(row[0]) == skill_id
    finally:
        conn.close()


def test_the_migration_run_is_idempotent(tmp_path: Path) -> None:
    """The idempotency that actually matters: running the RUNNER twice. The
    version guard is the mechanism, so this is the real contract — and boot runs
    migrations on every start."""
    db_path = tmp_path / "d.db"
    MigrationRunner(db_path=db_path).run()

    MigrationRunner(db_path=db_path).run()  # must not raise

    conn = sqlite3.connect(db_path)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(skills_fts)")}
        assert cols == {"name", "description", "when_to_use"}
    finally:
        conn.close()
