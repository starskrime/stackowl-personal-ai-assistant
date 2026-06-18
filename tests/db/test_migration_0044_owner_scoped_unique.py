"""Migration 0044 — owner-scoped uniqueness (Pass 2 multi-tenant correctness).

Verifies the three rebuilt tables (tool_heuristics, user_preferences, skills)
now carry a UNIQUE(owner_id, <original cols>) constraint instead of an
owner-blind global UNIQUE. Concretely:

* TWO different owner_ids CAN insert the same (original cols) with no
  IntegrityError (independent per-tenant rows).
* The SAME owner inserting the same (cols) collides — i.e. the upsert
  ON CONFLICT path is reachable on the new constraint.
* A row written BEFORE 0044 survives the table rebuild (data preserved).
* Every original index (plus the idx_<t>_owner indexes from 0043) still
  exists after the rebuild.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from stackowl.db.migrations.runner import MigrationRunner

# (table, original-unique-cols, an INSERT building a full valid row).
# Each insert provides every NOT-NULL column except owner_id (parametrised).
_HEURISTIC_COLS = "tool_name, condition_kind, condition_value, predicted_outcome"
_PREF_COLS = "owner_key, key"
_SKILL_COLS = "source, name"

# Indexes that must survive each rebuild (original + idx_<t>_owner from 0043).
_EXPECTED_INDEXES = {
    "tool_heuristics": {
        "idx_tool_heuristics_tool",
        "idx_tool_heuristics_outcome",
        "idx_tool_heuristics_evidence",
        "idx_tool_heuristics_owner",
    },
    "user_preferences": {
        "idx_user_preferences_owner",
        "idx_user_preferences_owner_key",
    },
    "skills": {
        "idx_skills_source",
        "idx_skills_enabled",
        "idx_skills_success_rate",
        "idx_skills_owner",
    },
}


def _migrate(tmp_path: Path) -> Path:
    db_path = tmp_path / "m.db"
    MigrationRunner(db_path=db_path).run()
    return db_path


def _insert_heuristic(conn: sqlite3.Connection, owner_id: str) -> None:
    conn.execute(
        "INSERT INTO tool_heuristics "
        "(tool_name, condition_kind, condition_value, predicted_outcome, "
        " last_seen_at, created_at, updated_at, owner_id) "
        "VALUES ('shell', 'arg', 'rm -rf', 'failure', 0.0, 0.0, 0.0, ?)",
        (owner_id,),
    )


def _insert_pref(conn: sqlite3.Connection, owner_id: str) -> None:
    conn.execute(
        "INSERT INTO user_preferences (owner_key, key, value, updated_at, owner_id) "
        "VALUES ('global', 'tier', 'powerful', 0.0, ?)",
        (owner_id,),
    )


def _insert_skill(conn: sqlite3.Connection, owner_id: str) -> None:
    conn.execute(
        "INSERT INTO skills (name, source, path, loaded_at, updated_at, owner_id) "
        "VALUES ('demo', 'community', '/p', 0.0, 0.0, ?)",
        (owner_id,),
    )


_INSERTERS = {
    "tool_heuristics": _insert_heuristic,
    "user_preferences": _insert_pref,
    "skills": _insert_skill,
}


@pytest.mark.parametrize("table", ["tool_heuristics", "user_preferences", "skills"])
def test_two_owners_can_hold_same_logical_key(tmp_path: Path, table: str) -> None:
    """Different owner_ids inserting the SAME original-key cols must not collide."""
    db_path = _migrate(tmp_path)
    insert = _INSERTERS[table]
    conn = sqlite3.connect(db_path)
    try:
        insert(conn, "principal-default")
        insert(conn, "principal-other")  # same logical key, different owner — OK
        conn.commit()
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608
        assert count == 2, f"{table}: expected 2 owner-scoped rows, got {count}"
    finally:
        conn.close()


@pytest.mark.parametrize("table", ["tool_heuristics", "user_preferences", "skills"])
def test_same_owner_same_key_collides(tmp_path: Path, table: str) -> None:
    """SAME owner re-inserting the SAME key must hit the unique constraint."""
    db_path = _migrate(tmp_path)
    insert = _INSERTERS[table]
    conn = sqlite3.connect(db_path)
    try:
        insert(conn, "principal-default")
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            insert(conn, "principal-default")
    finally:
        conn.close()


@pytest.mark.parametrize("table", ["tool_heuristics", "user_preferences", "skills"])
def test_indexes_survive_rebuild(tmp_path: Path, table: str) -> None:
    db_path = _migrate(tmp_path)
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = ?",
            (table,),
        ).fetchall()
        names = {r[0] for r in rows}
        missing = _EXPECTED_INDEXES[table] - names
        assert not missing, f"{table}: lost indexes after rebuild: {missing}"
    finally:
        conn.close()


def test_unique_constraint_now_includes_owner_id(tmp_path: Path) -> None:
    """The rebuilt UNIQUE index must lead with owner_id for each table."""
    db_path = _migrate(tmp_path)
    conn = sqlite3.connect(db_path)
    try:
        for table, original in (
            ("tool_heuristics", _HEURISTIC_COLS),
            ("user_preferences", _PREF_COLS),
            ("skills", _SKILL_COLS),
        ):
            # Find the auto-generated UNIQUE index and read its column list.
            idx_rows = conn.execute(
                f"PRAGMA index_list({table})"  # noqa: S608
            ).fetchall()
            unique_auto = [
                r[1] for r in idx_rows if r[2] == 1 and r[1].startswith("sqlite_autoindex")
            ]
            assert unique_auto, f"{table}: no auto UNIQUE index found"
            cols = [
                c[2]
                for c in conn.execute(f"PRAGMA index_info({unique_auto[0]})")  # noqa: S608
            ]
            assert cols[0] == "owner_id", f"{table}: UNIQUE does not lead with owner_id: {cols}"
            for orig_col in [c.strip() for c in original.split(",")]:
                assert orig_col in cols, f"{table}: UNIQUE missing original col {orig_col}"
    finally:
        conn.close()


def test_preexisting_row_survives_rebuild(tmp_path: Path) -> None:
    """A row written after 0043 but before 0044 survives the table rebuild."""
    db_path = tmp_path / "m.db"
    migrations_dir = Path(
        MigrationRunner(db_path=db_path)._migrations_dir  # noqa: SLF001
    )
    # Stage 1: run migrations < 0044 (so owner_id exists but UNIQUE is still global).
    staging = tmp_path / "pre0044"
    staging.mkdir()
    for sql in sorted(migrations_dir.glob("*.sql")):
        if sql.stem.split("_", 1)[0] < "0044":
            (staging / sql.name).write_text(
                sql.read_text(encoding="utf-8"), encoding="utf-8"
            )
    MigrationRunner(db_path=db_path, migrations_dir=staging).run()

    conn = sqlite3.connect(db_path)
    try:
        _insert_pref(conn, "legacy-owner")
        _insert_skill(conn, "legacy-owner")
        _insert_heuristic(conn, "legacy-owner")
        conn.commit()
    finally:
        conn.close()

    # Stage 2: run full runner (applies 0044 rebuild over the populated DB).
    MigrationRunner(db_path=db_path).run()

    conn = sqlite3.connect(db_path)
    try:
        for table in ("tool_heuristics", "user_preferences", "skills"):
            row = conn.execute(
                f"SELECT owner_id FROM {table} WHERE owner_id = 'legacy-owner'"  # noqa: S608
            ).fetchone()
            assert row is not None, f"{table}: legacy row lost in rebuild"
            assert row[0] == "legacy-owner"
    finally:
        conn.close()


def test_migration_0044_rerun_is_noop(tmp_path: Path) -> None:
    db_path = tmp_path / "m.db"
    MigrationRunner(db_path=db_path).run()
    results = MigrationRunner(db_path=db_path).run()
    applied = [r for r in results if r.action == "applied"]
    assert applied == [], f"re-run applied migrations: {applied}"
    rec = next(r for r in results if r.version == "0044")
    assert rec.action == "skipped"
