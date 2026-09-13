"""`stackowl db query` — read the live database, and never create a database file.

WHY THIS EXISTS. `~/.stackowl/stackowl.db` kept reappearing as a 0-byte file: ad-hoc
diagnosis guessed the database sits beside the config, connected there, and SQLite
created an empty file instead of failing. The real location (`StackowlHome.db_path()`)
was only discoverable by reading code, and the one safe reader was a shell helper under
`scripts/` documented only in its own header. This command replaced that helper and
keeps its contract:

  * rows on stdout, one per line, tab-separated, nothing else — so `| wc -l` counts
    rows. NULL is empty, a BLOB is hex, and a backslash, tab or newline inside TEXT is
    escaped (`\\\\`, `\\t`, `\\n`) so one row always stays one line;
  * a missing or empty database is an ERROR (exit 2), never a zero-row answer;
  * opened read-only, so a write fails by construction (exit 1).

It adds, on stderr, the path it resolved and `rows=N`. A reader that stops reading
(`| head -1`) is not an error.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import sys

import typer

log = logging.getLogger("stackowl.cli")

#: "There is no database to answer from" — distinct from a failed query, so a caller can
#: tell missing data from bad SQL. The shell helper this replaced used the same code.
_EXIT_NO_DATABASE = 2
_EXIT_QUERY_FAILED = 1

#: TEXT escaping that keeps one row on one line. Backslash first is implied: a
#: translation table maps each character once, so an inserted `\\` is never re-escaped.
_CELL_ESCAPES = str.maketrans({"\\": "\\\\", "\t": "\\t", "\n": "\\n"})


def _cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.hex()
    return str(value).translate(_CELL_ESCAPES)


def _silence_stdout() -> None:
    """Point stdout at the null device so the interpreter's final flush into a closed
    pipe does not print a traceback after the command has already exited cleanly."""
    try:
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
    except (OSError, ValueError) as exc:  # not a real descriptor (a test runner)
        log.debug("[cli] db_query: stdout has no descriptor to redirect: %s", exc)


def db_query(
    sql: str = typer.Argument(
        ..., help='One read-only SQL statement, e.g. "SELECT COUNT(*) FROM jobs".'
    ),
) -> None:
    """Run one read-only SQL statement against the live database and print the rows."""
    from stackowl.db.readonly import connect_read_only
    from stackowl.paths import StackowlHome

    db = StackowlHome.db_path()
    # 1. ENTRY
    log.debug("[cli] db_query: entry — db=%s", db)
    typer.echo(f"database: {db}", err=True)

    # 2. DECISION — a missing or empty database is an error, never an empty result set
    state = "missing" if not db.is_file() else ("empty" if db.stat().st_size == 0 else "")
    if state:
        log.warning("[cli] db_query: refused — the database is %s: %s", state, db)
        typer.echo(f"✗ {db} is {state} — this is not a zero-row answer", err=True)
        typer.echo(
            "  Remedy: this path is StackowlHome.db_path(), derived from STACKOWL_HOME and "
            "STACKOWL_DATA_DIR — check they point at the install you mean; a new install "
            "creates it with `stackowl init`",
            err=True,
        )
        raise typer.Exit(_EXIT_NO_DATABASE)

    # 3. STEP — read-only by construction
    try:
        conn = connect_read_only(db)
    except sqlite3.Error as exc:
        log.warning("[cli] db_query: could not open %s read-only: %s", db, exc)
        typer.echo(f"✗ could not open {db} read-only: {exc}", err=True)
        raise typer.Exit(_EXIT_QUERY_FAILED) from exc
    rows = 0
    try:
        cursor = conn.execute(sql)
        if cursor.description is None:
            # Empty, blank, comment-only, or a statement with no result columns: there
            # is nothing to read, and exiting 0 with no output would read as zero rows.
            log.warning("[cli] db_query: refused — the SQL returns no result columns")
            typer.echo(
                "✗ nothing to read: the SQL is empty, only a comment, or not a query "
                "that returns rows",
                err=True,
            )
            raise typer.Exit(_EXIT_QUERY_FAILED)
        for row in cursor:
            typer.echo("\t".join(_cell(col) for col in row))
            rows += 1
    except BrokenPipeError:
        log.debug("[cli] db_query: the reader closed the pipe after %d row(s)", rows)
        _silence_stdout()
        raise typer.Exit(0) from None
    except sqlite3.Error as exc:
        if str(getattr(exc, "sqlite_errorname", "")).startswith("SQLITE_READONLY"):
            log.warning("[cli] db_query: refused a write: %s", exc)
            typer.echo(f"✗ refused: this command is read-only ({exc})", err=True)
            typer.echo(
                "  Remedy: data changes go through migrations (`stackowl db migrate`), "
                "never an ad-hoc write",
                err=True,
            )
        else:
            log.warning("[cli] db_query: query failed: %s", exc)
            typer.echo(f"✗ query failed: {exc}", err=True)
            # SQLite's own fixed phrase for the attachment limit — see stackowl.db.readonly.
            if "attached databases" in str(exc):
                typer.echo(
                    "  This command reads one database: ATTACH and VACUUM INTO are refused "
                    "because either can create a new file.",
                    err=True,
                )
        raise typer.Exit(_EXIT_QUERY_FAILED) from exc
    finally:
        conn.close()

    # 4. EXIT
    typer.echo(f"rows={rows}", err=True)
    log.debug("[cli] db_query: exit — rows=%d", rows)
