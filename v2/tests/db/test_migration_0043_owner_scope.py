"""Migration 0043 — owner_id retrofit across user-data tables (Pass 1).

Verifies every retrofitted table gains an owner_id column defaulting to
'principal-default', that a pre-existing row backfills to the default, that
shareable entities also gain visibility, that framework tables are NOT
retrofitted, and that a re-run is a clean no-op.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from stackowl.db.migrations.runner import MigrationRunner
from stackowl.tenancy.principal import DEFAULT_PRINCIPAL_ID

# Tables that migration 0043 retrofits with owner_id.
RETROFITTED = [
    "conversations",
    "messages",
    "memory_facts",
    "staged_facts",
    "committed_facts",
    "fact_rejections",
    "owl_profiles",
    "owl_dna",
    "dna_checkpoints",
    "pellets",
    "parliament_sessions",
    "cost_records",
    "task_outcomes",
    "reflections",
    "tool_heuristics",
    "user_preferences",
    "onboarding",
    "skills",
]

# Framework / infra tables that must NOT be retrofitted.
SKIPPED = [
    "stackowl_meta",
    "schema_migrations",
    "audit_log",
    "callback_log",
    "plugins",
    "skill_audit",
    "thread_registry",
    "notification_queue",
    "reindex_queue",
    "dreamworker_runs",
]

# Shareable entities that also gain a visibility column.
HAS_VISIBILITY = ["owl_profiles", "skills"]


def _columns(db_path: Path, table: str) -> dict[str, sqlite3.Row]:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        # (cid, name, type, notnull, dflt_value, pk)
        return {r[1]: r for r in rows}
    finally:
        conn.close()


def test_all_retrofitted_tables_have_owner_id(tmp_path: Path) -> None:
    db_path = tmp_path / "m.db"
    MigrationRunner(db_path=db_path).run()
    for table in RETROFITTED:
        cols = _columns(db_path, table)
        assert "owner_id" in cols, f"{table} missing owner_id"
        notnull = cols["owner_id"][3]
        default = cols["owner_id"][4]
        assert notnull == 1, f"{table}.owner_id should be NOT NULL"
        assert str(default).strip("'") == DEFAULT_PRINCIPAL_ID, (
            f"{table}.owner_id default is {default!r}"
        )


def test_skipped_tables_have_no_owner_id(tmp_path: Path) -> None:
    db_path = tmp_path / "m.db"
    MigrationRunner(db_path=db_path).run()
    for table in SKIPPED:
        cols = _columns(db_path, table)
        assert "owner_id" not in cols, f"{table} should NOT have owner_id"


def test_shareable_tables_have_visibility(tmp_path: Path) -> None:
    db_path = tmp_path / "m.db"
    MigrationRunner(db_path=db_path).run()
    for table in HAS_VISIBILITY:
        cols = _columns(db_path, table)
        assert "visibility" in cols, f"{table} missing visibility"
        assert str(cols["visibility"][4]).strip("'") == "private"


def test_preexisting_row_backfills_to_default(tmp_path: Path) -> None:
    """A row written BEFORE 0043 backfills to principal-default after migrate."""
    db_path = tmp_path / "m.db"
    # Apply everything up to but excluding 0043 by running the full runner once
    # is simplest: instead simulate a legacy row by inserting before 0043 cannot
    # be done mid-run, so we insert into a table created by an earlier migration
    # using a partial runner. Run all migrations EXCEPT 0043, insert, then 0043.
    migrations_dir = Path(
        MigrationRunner(db_path=db_path)._migrations_dir  # noqa: SLF001
    )
    # Stage 1: copy migrations < 0043 into a temp dir and run them.
    staging = tmp_path / "pre0043"
    staging.mkdir()
    for sql in sorted(migrations_dir.glob("*.sql")):
        if sql.stem.split("_", 1)[0] < "0043":
            (staging / sql.name).write_text(
                sql.read_text(encoding="utf-8"), encoding="utf-8"
            )
    MigrationRunner(db_path=db_path, migrations_dir=staging).run()

    # Insert a legacy reflection row (table exists at 0030, pre-0043, no owner_id).
    conn = sqlite3.connect(db_path)
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(user_preferences)")]
        assert "owner_id" not in cols  # confirm we are genuinely pre-retrofit
        conn.execute(
            "INSERT INTO user_preferences (owner_key, key, value, updated_at) "
            "VALUES ('legacy-owner', 'legacy', 'v', 0.0)"
        )
        conn.commit()
    finally:
        conn.close()

    # Stage 2: run the full runner (applies 0043 over the legacy DB).
    MigrationRunner(db_path=db_path).run()

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT owner_id FROM user_preferences WHERE key = 'legacy'"
        ).fetchone()
        assert row is not None
        assert row[0] == DEFAULT_PRINCIPAL_ID
    finally:
        conn.close()


def test_migration_rerun_is_noop(tmp_path: Path) -> None:
    db_path = tmp_path / "m.db"
    MigrationRunner(db_path=db_path).run()
    results = MigrationRunner(db_path=db_path).run()
    applied = [r for r in results if r.action == "applied"]
    assert applied == [], f"re-run applied migrations: {applied}"
    rec = next(r for r in results if r.version == "0043")
    assert rec.action == "skipped"
