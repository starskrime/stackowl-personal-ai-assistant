"""DUR-1 / F020 — _split_sql must treat BEGIN/END as trigger-body delimiters
only, never counting the words inside CASE…END expressions, string literals,
comments, or transaction control.

Locks the tokenizer-aware splitter so a future migration carrying a
``CASE…END`` column default, a ``RAISE(ABORT,'…the end')`` string, or a
``BEGIN``/``COMMIT`` line applies as the intended statements rather than being
glued into one malformed blob.
"""

from __future__ import annotations

from stackowl.db.migrations.runner import _split_sql


def test_case_end_default_is_not_a_trigger_body() -> None:
    """A CASE…END inside a column default must not start a trigger block.

    Two real statements separated by ``;`` must split into two — the bare
    ``\\bEND\\b`` counter would have driven depth negative and mis-merged.
    """
    sql = """
    CREATE TABLE t (
        id INTEGER PRIMARY KEY,
        kind TEXT GENERATED ALWAYS AS (
            CASE WHEN id > 0 THEN 'pos' ELSE 'neg' END
        ) VIRTUAL
    );
    CREATE TABLE u (id INTEGER PRIMARY KEY);
    """
    stmts = _split_sql(sql)
    assert len(stmts) == 2  # noqa: PLR2004
    assert stmts[0].lstrip().upper().startswith("CREATE TABLE T")
    assert stmts[1].lstrip().upper().startswith("CREATE TABLE U")


def test_end_word_in_string_literal_does_not_split() -> None:
    """The word ``end`` inside a quoted string is not a delimiter."""
    sql = """
    INSERT INTO log (msg) VALUES ('this is the end; begin again');
    CREATE TABLE after_it (id INTEGER);
    """
    stmts = _split_sql(sql)
    assert len(stmts) == 2  # noqa: PLR2004
    assert "the end; begin again" in stmts[0]


def test_create_trigger_body_stays_atomic() -> None:
    """A real CREATE TRIGGER…BEGIN…END block is one statement with its inner ;."""
    sql = """
    CREATE TRIGGER guard BEFORE UPDATE ON audit_log
    BEGIN
        SELECT RAISE(ABORT, 'append-only; the end');
    END;
    CREATE TABLE done (id INTEGER);
    """
    stmts = _split_sql(sql)
    assert len(stmts) == 2  # noqa: PLR2004
    assert "CREATE TRIGGER" in stmts[0]
    # the inner statement-terminating ; stayed inside the trigger body
    assert "RAISE(ABORT" in stmts[0]
    assert stmts[1].lstrip().upper().startswith("CREATE TABLE DONE")


def test_transaction_begin_commit_not_counted_as_trigger() -> None:
    """Bare BEGIN/COMMIT transaction control is not a trigger body."""
    sql = """
    CREATE TABLE a (id INTEGER);
    CREATE TABLE b (id INTEGER);
    """
    stmts = _split_sql(sql)
    assert len(stmts) == 2  # noqa: PLR2004


def test_line_comment_with_end_word_ignored() -> None:
    """``-- end of section`` comment text must not flip the trigger depth."""
    sql = """
    CREATE TABLE c (id INTEGER); -- end of section, begin next
    CREATE TABLE d (id INTEGER);
    """
    stmts = _split_sql(sql)
    assert len(stmts) == 2  # noqa: PLR2004


def test_block_comment_with_begin_end_ignored() -> None:
    """A /* BEGIN … END */ block comment must not open a trigger body."""
    sql = """
    CREATE TABLE e (id INTEGER); /* BEGIN block END block */
    CREATE TABLE f (id INTEGER);
    """
    stmts = _split_sql(sql)
    assert len(stmts) == 2  # noqa: PLR2004
