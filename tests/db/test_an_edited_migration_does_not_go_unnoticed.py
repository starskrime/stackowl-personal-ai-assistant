"""D18.9 — the runner stored a checksum for 136 migrations and never read one.

`MigrationRunner._apply` computes `hashlib.sha256(sql)` and writes it to
`schema_migrations.checksum`. Measured 2026-09-05: **nothing in `src/` reads that
column back.** A value computed, stored, and never compared is this repo's shape #1,
a write with no reader — and the thing it was created to detect is real. An applied
migration is skipped by version, so editing its file changes what a FRESH install
gets while the existing database keeps the old schema. Two databases, same version
number, different shapes, silently.

**IT HAS ALREADY HAPPENED.** Comparing every stored checksum against its file:
130 matched and **6 drifted** — `0091_turn_metrics`, `0093_session_key_rename`,
`0099_lane_on_work`, `0100_runner_lane_parent`, `0102_session_prompts`,
`0106_messages_role_user_assistant_only`. All six were edited by one commit,
`419493a3 "refactor: no vendor names in shipped code"`, and a diff analysis found
**0 SQL-statement lines changed** in every one. So no schema diverged. Nothing would
have distinguished that from a change that did.

**AND THAT IS WHY THE READER WAS NEVER WIRED: a byte hash was unwireable.** Turning
it on would have reported six drifts on the first boot, every one of them a comment
edit. A guard that fires on correct code teaches its reader to ignore it — the same
lesson D18.7 recorded for the cross-platform checker, one item earlier.

So the hash covers what the migration DOES. `_split_sql` — the very function the
runner uses to EXECUTE — gained a `keep_comments=False` mode, so the semantic hash
is taken from the same walk that already knows a `--` inside a string literal is not
a comment. Measured: all six drifted files hash IDENTICALLY before and after that
commit, while the byte hash called all six drift.
"""

from __future__ import annotations

import hashlib

import pytest

from stackowl.db.migrations.runner import _split_sql, semantic_checksum


def test_a_comment_edit_does_not_look_like_a_schema_change() -> None:
    """The false positive that kept the guard switched off."""
    before = "-- old note\nCREATE TABLE t (a INTEGER);\n"
    after = "-- a completely different note\nCREATE TABLE t (a INTEGER);\n"

    assert semantic_checksum(before) == semantic_checksum(after)
    assert hashlib.sha256(before.encode()).hexdigest() != hashlib.sha256(
        after.encode()
    ).hexdigest(), "the byte hash must differ, or this test proves nothing"


def test_a_real_schema_change_is_caught() -> None:
    """The whole point. Whitespace and comments are noise; a column is not."""
    before = "CREATE TABLE t (a INTEGER);\n"
    after = "CREATE TABLE t (a INTEGER, b TEXT);\n"

    assert semantic_checksum(before) != semantic_checksum(after)


def test_commenting_out_a_statement_is_a_schema_change() -> None:
    """The sharp edge of normalising comments away.

    Dropping comment TEXT must not drop a statement that has been commented OUT —
    that is a semantic change wearing a comment's clothes, and a naive "strip
    comments then compare" would be blind to it in exactly the wrong direction.
    """
    before = "CREATE TABLE t (a INTEGER);\nALTER TABLE t ADD COLUMN b TEXT;\n"
    after = "CREATE TABLE t (a INTEGER);\n-- ALTER TABLE t ADD COLUMN b TEXT;\n"

    assert semantic_checksum(before) != semantic_checksum(after)


def test_a_comment_marker_inside_a_string_literal_is_not_a_comment() -> None:
    """Why this reuses the runner's own tokenizer instead of a regex.

    `'--'` inside a quoted string is DATA. A regex that strips from `--` to
    end-of-line would silently truncate the statement and hash something that was
    never in the file — the guard corrupting its own evidence.
    """
    sql = "INSERT INTO t (a) VALUES ('-- not a comment');\n"
    statements = _split_sql(sql, keep_comments=False)

    assert "-- not a comment" in "".join(statements), (
        "the string literal was mangled — a comment marker inside quotes is data"
    )
    assert semantic_checksum(sql) != semantic_checksum("INSERT INTO t (a) VALUES ('');\n")


def test_statement_ORDER_is_part_of_the_meaning() -> None:
    """Reordering statements must change the hash.

    Found by mutation testing, and it was a missing TEST rather than a missing
    behaviour: sorting the statements before hashing broke nothing in the suite. It
    should have. `CREATE TABLE t` followed by `ALTER TABLE t ADD COLUMN` is a
    migration; the reverse is an error, and a hash that called them equal would
    bless a reordering that cannot run.
    """
    forward = "CREATE TABLE t (a INTEGER);\nALTER TABLE t ADD COLUMN b TEXT;\n"
    reversed_ = "ALTER TABLE t ADD COLUMN b TEXT;\nCREATE TABLE t (a INTEGER);\n"

    assert semantic_checksum(forward) != semantic_checksum(reversed_)


def test_indentation_and_line_breaks_are_not_schema() -> None:
    """Whitespace RUNS are collapsed, which covers how these files actually change.

    THE LIMIT IS STATED RATHER THAN OVERSOLD. This normalises runs of whitespace, so
    re-indenting a statement or moving it onto one line does not register. It does
    NOT equalise spacing adjacent to punctuation — `t (a INTEGER)` and
    `t ( a INTEGER )` hash differently — because doing so would mean a second walk
    that has to know which parentheses are inside a string literal, and the whole
    point of reusing `_split_sql` was to avoid owning a second tokenizer.

    A full re-layout of an ALREADY-APPLIED migration therefore reports drift. That
    is the right side to err on: rewriting the text of a migration the database has
    already run is worth a human look, and the warning says which file.
    """
    a = "CREATE TABLE t (a INTEGER,\n     b TEXT);\n"
    b = "CREATE TABLE t (a INTEGER, b TEXT);\n"
    assert semantic_checksum(a) == semantic_checksum(b)


@pytest.mark.parametrize(
    "name",
    [
        "0091_turn_metrics",
        "0093_session_key_rename",
        "0099_lane_on_work",
        "0100_runner_lane_parent",
        "0102_session_prompts",
        "0106_messages_role_user_assistant_only",
    ],
)
def test_the_six_real_drifts_were_comment_only(name: str) -> None:
    """The measurement this whole design rests on, pinned against the real files.

    These six are the actual drifted migrations in the live database. If a future
    edit makes one of them semantically different from what was applied, this fails
    — which is the point: the claim "no schema diverged" must stay checkable rather
    than remain a sentence in a document.
    """
    import subprocess
    from pathlib import Path

    rel = f"src/stackowl/db/migrations/{name}.sql"
    old = subprocess.run(  # noqa: S603
        ["git", "show", f"419493a3~1:{rel}"], capture_output=True, text=True, check=True
    ).stdout
    new = Path(rel).read_text(encoding="utf-8")

    assert semantic_checksum(old) == semantic_checksum(new), (
        f"{name} changed SEMANTICALLY in the vendor-name refactor — the live database "
        "and a fresh install would have diverged"
    )


def test_the_runner_backfills_then_notices_a_real_edit(tmp_path, caplog) -> None:
    """The READER, end to end — the half that was missing for 136 migrations.

    A static hash test proves the arithmetic; it does not prove anything reads it.
    This runs the real runner over a real (tiny) migration directory, then edits an
    APPLIED migration and runs again.
    """
    import logging

    from stackowl.db.migrations.runner import MigrationRunner

    migrations = tmp_path / "migrations"
    migrations.mkdir()
    # `stackowl_meta` is what the runner advances its schema_version pointer in;
    # the real 0001 creates it, so a stand-in directory must too.
    _META = (
        "CREATE TABLE IF NOT EXISTS stackowl_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);\n"
    )
    (migrations / "0001_initial.sql").write_text(
        _META + "\nCREATE TABLE widget (id INTEGER PRIMARY KEY);\n", encoding="utf-8"
    )
    db = tmp_path / "test.db"

    MigrationRunner(db_path=db, migrations_dir=migrations).run()

    import sqlite3

    conn = sqlite3.connect(db)
    stored = conn.execute("SELECT sql_checksum FROM schema_migrations").fetchone()
    conn.close()
    assert stored and stored[0], "the runner did not baseline sql_checksum"

    # A COMMENT-ONLY edit must stay silent — the false alarm that kept this off.
    (migrations / "0001_initial.sql").write_text(
        "-- a new note\n" + _META + "\nCREATE TABLE widget (id INTEGER PRIMARY KEY);\n",
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING, logger="stackowl.db"):
        MigrationRunner(db_path=db, migrations_dir=migrations).run()
    assert not [r for r in caplog.records if "CHANGED since they ran" in r.getMessage()], (
        "a comment edit was reported as schema drift — the exact false alarm that "
        "kept the byte checksum switched off"
    )

    # A REAL edit must be reported, by name.
    caplog.clear()
    (migrations / "0001_initial.sql").write_text(
        _META + "\nCREATE TABLE widget (id INTEGER PRIMARY KEY, extra TEXT);\n",
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING, logger="stackowl.db"):
        MigrationRunner(db_path=db, migrations_dir=migrations).run()
    warned = [r.getMessage() for r in caplog.records if "CHANGED since they ran" in r.getMessage()]
    assert warned, "an applied migration was edited semantically and nothing said so"
    assert "0001_initial.sql" in warned[0], "the warning must name the file"
