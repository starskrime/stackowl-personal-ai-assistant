"""FTS over messages must find what substring search structurally cannot.

D11.2. session_search's `discover` built ONE pattern from the whole query —
LIKE '%<entire query>%' — so a multi-word query only matched when those exact
characters sat adjacent. MEASURED 2026-08-29: of six real `discover` calls,
THREE returned zero rows.

AND THE MIRROR IS TRIGGER-SYNCED, which this tree already proved it must be.
There are two triggers in the live database and both guard audit_log; every
existing FTS mirror is synced by application code, and one of them is ALREADY
DEAD — committed_facts_fts holds 0 rows while its shadow tables still carry
1,112, a stale index of content that no longer exists, because D08.1 removed the
writer and nothing told the mirror.

external content (content='messages') keeps the text stored once, so `messages`
stays the single source of truth. This is an index over the existing store, not
the second store session_search's docstring rightly refused to stand up.
"""

from __future__ import annotations

import pathlib
import sqlite3
import tempfile

from stackowl.db.migrations.runner import MigrationRunner


def _db() -> sqlite3.Connection:
    path = pathlib.Path(tempfile.mkdtemp()) / "t.db"
    MigrationRunner(path).run()
    conn = sqlite3.connect(path)
    conn.execute("DELETE FROM messages")
    conn.execute("DELETE FROM messages_fts")
    return conn


def _msg(conn: sqlite3.Connection, mid: str, text: str) -> None:
    conn.execute(
        "INSERT INTO messages (id, conversation_id, role, content, created_at, owner_id) "
        "VALUES (?, 'conv-1', 'user', ?, '2026-08-29T00:00:00', 'principal-default')",
        (mid, text),
    )
    conn.commit()


def _fts(conn: sqlite3.Connection, q: str) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT m.content FROM messages_fts f JOIN messages m ON m.rowid = f.rowid "
        "WHERE messages_fts MATCH ? ORDER BY rank", (q,)
    )]


def _like(conn: sqlite3.Connection, q: str) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT content FROM messages WHERE content LIKE ?", (f"%{q}%",)
    )]


def test_a_multi_word_query_finds_the_turn_LIKE_misses() -> None:
    """THE regression, and the measured 50% zero-result rate.

    The words are both present and NOT adjacent — the single case substring
    search can never handle.
    """
    conn = _db()
    _msg(conn, "m1", "the browser kept hitting a timeout on that page")

    assert _like(conn, "browser timeout") == [], (
        "the fixture does not reproduce the bug — LIKE must miss this"
    )
    assert len(_fts(conn, "browser timeout")) == 1, (
        "FTS did not find a turn containing both words"
    )


def test_a_deleted_message_stops_matching() -> None:
    """The half an application-synced mirror gets wrong.

    committed_facts_fts still indexes 1,112 rows of content that no longer
    exists. A deleted message must leave the index too, or search returns turns
    that are gone.
    """
    conn = _db()
    _msg(conn, "m2", "a uniquely findable phrase about kangaroos")
    assert _fts(conn, "kangaroos")

    conn.execute("DELETE FROM messages WHERE id = 'm2'")
    conn.commit()

    assert _fts(conn, "kangaroos") == [], "the index still matches a deleted message"


def test_an_edited_message_matches_its_NEW_text() -> None:
    """An update must replace, not accumulate — otherwise old text matches for ever."""
    conn = _db()
    _msg(conn, "m3", "originally about penguins")

    conn.execute("UPDATE messages SET content = 'now about walruses' WHERE id = 'm3'")
    conn.commit()

    assert _fts(conn, "walruses"), "the new text is not searchable"
    assert _fts(conn, "penguins") == [], "the OLD text still matches after an edit"


def test_the_backfill_covered_rows_that_already_existed() -> None:
    """A mirror that only indexes NEW messages leaves every past turn unfindable —
    which is precisely the cross-session recall this item exists for."""
    path = pathlib.Path(tempfile.mkdtemp()) / "t.db"
    MigrationRunner(path).run()
    conn = sqlite3.connect(path)

    indexed = conn.execute("SELECT COUNT(*) FROM messages_fts").fetchone()[0]
    rows = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE content IS NOT NULL"
    ).fetchone()[0]

    assert indexed == rows, f"backfill indexed {indexed} of {rows} existing messages"
