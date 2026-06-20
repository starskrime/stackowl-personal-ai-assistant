"""Tests for `stackowl identity link` — cross-channel identity re-key CLI.

Uses the `relink()` helper directly (pure SQLite, no migrations needed) with
real temp SQLite DBs created inline. The tables are created by hand so the test
is fast and free of migration coupling.

Invariants under test:
  1. relink() re-keys user_preferences.owner_key for alias handles.
  2. relink() re-keys staged_facts.source_ref for source_type != 'conversation'.
  3. staged_facts rows with source_type == 'conversation' are NEVER re-keyed.
  4. A second relink() call returns zero counts (idempotent).
  5. dry_run=True reports nonzero counts but leaves the DB unchanged.
  6. relink() re-keys committed_facts.source_ref for source_type != 'conversation'.
  7. committed_facts rows with source_type == 'conversation' are NEVER re-keyed.
  8. A second relink() on committed_facts returns zero 'committed' count (idempotent).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from stackowl.cli.identity_cli import relink

# ── Schema helpers ─────────────────────────────────────────────────────────────

_DDL_PREFERENCES = """
CREATE TABLE IF NOT EXISTS user_preferences (
    owner_id TEXT NOT NULL DEFAULT 'principal-default',
    owner_key TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    updated_at REAL NOT NULL DEFAULT 0.0,
    UNIQUE(owner_id, owner_key, key)
)
"""

_DDL_FACTS = """
CREATE TABLE IF NOT EXISTS staged_facts (
    fact_id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    source_type TEXT NOT NULL DEFAULT 'conversation',
    source_ref TEXT NOT NULL DEFAULT '',
    owner_id TEXT NOT NULL DEFAULT 'principal-default'
)
"""

# committed_facts mirrors the real schema from migration 0014 + 0043 + 0052 + 0062.
# committed_facts has NO source_type CHECK constraint (only staged_facts does),
# so source_type='conversation' is a valid value here and must be excluded by the
# relink() WHERE clause, not by a schema guard.
_DDL_COMMITTED_FACTS = """
CREATE TABLE IF NOT EXISTS committed_facts (
    fact_id             TEXT    NOT NULL PRIMARY KEY,
    content             TEXT    NOT NULL,
    embedding           BLOB    NOT NULL,
    embedding_model     TEXT    NOT NULL,
    committed_at        TEXT    NOT NULL,
    source_type         TEXT    NOT NULL,
    source_ref          TEXT    NOT NULL,
    tags                TEXT    NOT NULL DEFAULT '[]',
    owner_id            TEXT    NOT NULL DEFAULT 'principal-default',
    trust               TEXT    NOT NULL DEFAULT 'untrusted',
    reinforcement_count INTEGER NOT NULL DEFAULT 0
)
"""


def _make_db(path: Path) -> str:
    """Create the three tables and return the path as a string."""
    conn = sqlite3.connect(str(path))
    conn.execute(_DDL_PREFERENCES)
    conn.execute(_DDL_FACTS)
    conn.execute(_DDL_COMMITTED_FACTS)
    conn.commit()
    conn.close()
    return str(path)


def _seed(db_str: str) -> None:
    """Insert one preference row, one conversation_fact row, one conversation (control) row."""
    conn = sqlite3.connect(db_str)
    conn.execute(
        "INSERT INTO user_preferences (owner_id, owner_key, key, value) VALUES (?,?,?,?)",
        ("principal-default", "telegram:123", "theme", "dark"),
    )
    conn.execute(
        "INSERT INTO staged_facts (fact_id, content, source_type, source_ref, owner_id)"
        " VALUES (?,?,?,?,?)",
        ("f1", "likes coffee", "conversation_fact", "telegram:123", "principal-default"),
    )
    # control: source_type='conversation' must NOT be re-keyed
    conn.execute(
        "INSERT INTO staged_facts (fact_id, content, source_type, source_ref, owner_id)"
        " VALUES (?,?,?,?,?)",
        ("f2", "ctrl", "conversation", "telegram:123", "principal-default"),
    )
    conn.commit()
    conn.close()


def _fetch(db_str: str, table: str, col: str, where: str, params: tuple) -> list:
    conn = sqlite3.connect(db_str)
    rows = conn.execute(f"SELECT {col} FROM {table} WHERE {where}", params).fetchall()
    conn.close()
    return [r[0] for r in rows]


# ── Tests ──────────────────────────────────────────────────────────────────────


def test_relink_re_keys_preference_row(tmp_path: Path) -> None:
    """relink() updates user_preferences.owner_key from handle to identity."""
    db = _make_db(tmp_path / "test.db")
    _seed(db)

    counts = relink(db, {"owner-primary": ["telegram:123"]}, "principal-default", dry_run=False)

    assert counts["preferences"] == 1
    # The row is now keyed on the identity, not the channel handle
    keys = _fetch(db, "user_preferences", "owner_key", "owner_id=?", ("principal-default",))
    assert keys == ["owner-primary"], f"expected ['owner-primary'], got {keys}"


def test_relink_re_keys_conversation_fact_but_not_conversation(tmp_path: Path) -> None:
    """relink() re-keys source_type='conversation_fact' but leaves source_type='conversation'."""
    db = _make_db(tmp_path / "test.db")
    _seed(db)

    counts = relink(db, {"owner-primary": ["telegram:123"]}, "principal-default", dry_run=False)

    assert counts["facts"] == 1  # only the conversation_fact row

    # conversation_fact → re-keyed to identity
    fact_refs = _fetch(
        db, "staged_facts", "source_ref",
        "fact_id=? AND source_type=?", ("f1", "conversation_fact"),
    )
    assert fact_refs == ["owner-primary"], f"expected identity key, got {fact_refs}"

    # conversation row → UNCHANGED (session isolation)
    ctrl_refs = _fetch(
        db, "staged_facts", "source_ref",
        "fact_id=? AND source_type=?", ("f2", "conversation"),
    )
    assert ctrl_refs == ["telegram:123"], (
        f"conversation row must NOT be re-keyed; got {ctrl_refs}"
    )


def test_relink_idempotent(tmp_path: Path) -> None:
    """A second relink() call returns zero counts (already re-keyed)."""
    db = _make_db(tmp_path / "test.db")
    _seed(db)

    relink(db, {"owner-primary": ["telegram:123"]}, "principal-default", dry_run=False)
    counts2 = relink(db, {"owner-primary": ["telegram:123"]}, "principal-default", dry_run=False)

    assert counts2 == {"preferences": 0, "facts": 0, "committed": 0}, (
        f"second relink must be zero-op; got {counts2}"
    )


def test_relink_dry_run_reports_counts_leaves_db_unchanged(tmp_path: Path) -> None:
    """dry_run=True reports nonzero counts but rolls back — DB is unchanged."""
    db = _make_db(tmp_path / "test.db")
    _seed(db)

    counts = relink(db, {"owner-primary": ["telegram:123"]}, "principal-default", dry_run=True)

    # Would-change counts are reported
    assert counts["preferences"] == 1
    assert counts["facts"] == 1

    # DB is unchanged — the original handle is still present
    keys = _fetch(db, "user_preferences", "owner_key", "owner_id=?", ("principal-default",))
    assert keys == ["telegram:123"], (
        f"dry_run must not commit; expected ['telegram:123'], got {keys}"
    )
    fact_refs = _fetch(
        db, "staged_facts", "source_ref",
        "fact_id=?", ("f1",),
    )
    assert fact_refs == ["telegram:123"], (
        f"dry_run must not commit staged_facts; got {fact_refs}"
    )


def test_relink_empty_aliases_returns_zero(tmp_path: Path) -> None:
    """An empty alias map produces zero counts and leaves DB untouched."""
    db = _make_db(tmp_path / "test.db")
    _seed(db)

    counts = relink(db, {}, "principal-default", dry_run=False)

    assert counts == {"preferences": 0, "facts": 0, "committed": 0}


def test_relink_owner_scoped_does_not_touch_other_owners(tmp_path: Path) -> None:
    """relink() only touches rows for the given owner_id."""
    db = _make_db(tmp_path / "test.db")
    conn = sqlite3.connect(db)
    # Seed a row under a DIFFERENT owner_id
    conn.execute(
        "INSERT INTO user_preferences (owner_id, owner_key, key, value) VALUES (?,?,?,?)",
        ("other-owner", "telegram:123", "theme", "light"),
    )
    conn.commit()
    conn.close()

    counts = relink(db, {"owner-primary": ["telegram:123"]}, "principal-default", dry_run=False)

    assert counts["preferences"] == 0  # principal-default had no rows
    # other-owner's row is unchanged
    keys = _fetch(db, "user_preferences", "owner_key", "owner_id=?", ("other-owner",))
    assert keys == ["telegram:123"]


# ── committed_facts tests (FIX I1) ────────────────────────────────────────────


def _seed_committed(db_str: str, *, include_conversation_row: bool = True) -> None:
    """Seed committed_facts rows for FIX I1 tests."""
    conn = sqlite3.connect(db_str)
    # A promoted fact from a Telegram channel — should be re-keyed.
    conn.execute(
        "INSERT INTO committed_facts"
        " (fact_id, content, embedding, embedding_model, committed_at,"
        "  source_type, source_ref, owner_id)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (
            "cf1",
            "likes dark mode",
            b"\x00" * 4,  # minimal valid blob
            "test-model",
            "2026-01-01T00:00:00",
            "conversation_fact",
            "telegram:123",
            "principal-default",
        ),
    )
    if include_conversation_row:
        # A conversation row — committed_facts has NO source_type CHECK constraint
        # so this is valid; the relink WHERE clause must exclude it explicitly.
        conn.execute(
            "INSERT INTO committed_facts"
            " (fact_id, content, embedding, embedding_model, committed_at,"
            "  source_type, source_ref, owner_id)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (
                "cf2",
                "raw turn",
                b"\x00" * 4,
                "test-model",
                "2026-01-01T00:00:00",
                "conversation",
                "telegram:123",
                "principal-default",
            ),
        )
    conn.commit()
    conn.close()


def test_relink_re_keys_committed_facts(tmp_path: Path) -> None:
    """relink() updates committed_facts.source_ref from handle to identity."""
    db = _make_db(tmp_path / "test.db")
    _seed_committed(db, include_conversation_row=False)

    counts = relink(
        db, {"owner-primary": ["telegram:123"]}, "principal-default", dry_run=False
    )

    assert "committed" in counts, f"return dict must include 'committed' key; got {counts}"
    assert counts["committed"] == 1, f"expected 1 committed row re-keyed; got {counts['committed']}"

    refs = _fetch(
        db, "committed_facts", "source_ref",
        "fact_id=? AND source_type=?", ("cf1", "conversation_fact"),
    )
    assert refs == ["owner-primary"], (
        f"committed_facts source_ref must be re-keyed to identity; got {refs}"
    )


def test_relink_committed_facts_idempotent(tmp_path: Path) -> None:
    """A second relink() on committed_facts returns zero 'committed' count."""
    db = _make_db(tmp_path / "test.db")
    _seed_committed(db, include_conversation_row=False)

    relink(db, {"owner-primary": ["telegram:123"]}, "principal-default", dry_run=False)
    counts2 = relink(db, {"owner-primary": ["telegram:123"]}, "principal-default", dry_run=False)

    assert counts2.get("committed", 0) == 0, (
        f"second relink must be zero-op on committed_facts; got {counts2}"
    )


def test_relink_committed_facts_excludes_conversation_rows(tmp_path: Path) -> None:
    """committed_facts rows with source_type='conversation' must NOT be re-keyed.

    committed_facts has no source_type CHECK constraint (unlike staged_facts),
    so source_type='conversation' is a valid value. The relink WHERE clause
    must explicitly exclude it.
    """
    db = _make_db(tmp_path / "test.db")
    _seed_committed(db, include_conversation_row=True)

    counts = relink(
        db, {"owner-primary": ["telegram:123"]}, "principal-default", dry_run=False
    )

    # Only cf1 (conversation_fact) should be re-keyed; cf2 (conversation) must not.
    assert counts.get("committed", 0) == 1, (
        f"only the non-conversation committed row should be re-keyed; got {counts}"
    )

    # conversation row untouched
    ctrl_refs = _fetch(
        db, "committed_facts", "source_ref",
        "fact_id=? AND source_type=?", ("cf2", "conversation"),
    )
    assert ctrl_refs == ["telegram:123"], (
        f"committed_facts conversation row must NOT be re-keyed; got {ctrl_refs}"
    )
