"""`stackowl db query` reads the live database and can never create one.

WHY THIS EXISTS. `~/.stackowl/stackowl.db` kept reappearing as a 0-byte file because
ad-hoc diagnosis guessed the database sits beside the config and ran
`sqlite3.connect()` there, and SQLite creates a missing file instead of failing. The
real location is only discoverable by reading `StackowlHome`, and the one safe helper
lived in a shell script documented only in its own header.

THIS COMMAND REPLACES THAT SCRIPT and keeps its contract: one row per line, tab-separated,
NULL rendered empty, nothing else on stdout (so `| wc -l` means what it says), and a
missing or empty database is an ERROR rather than a zero-row answer. The resolved path
goes to stderr, so a caller always sees which file answered.

`mode=ro` IS NOT ENOUGH ON ITS OWN. Measured 2026-09-12: on a read-only connection
`ATTACH '<new>.db'` and `VACUUM INTO '<new>.db'` both still create a new database file.
Refusing attachments closes both, and the two cases are pinned below.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from stackowl.cli.app import app
from stackowl.paths import StackowlHome

_ROOT = Path(__file__).resolve().parents[2]
runner = CliRunner()


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "home"
    monkeypatch.setenv("STACKOWL_HOME", str(root))
    monkeypatch.delenv("STACKOWL_DATA_DIR", raising=False)
    return root


@pytest.fixture()
def live_db(home: Path) -> Path:
    db = StackowlHome.db_path()
    db.parent.mkdir(parents=True)
    conn = sqlite3.connect(db)
    try:
        conn.executescript(
            "PRAGMA journal_mode=WAL;"
            "CREATE TABLE jobs (id INTEGER, name TEXT);"
            "INSERT INTO jobs VALUES (1, 'a'), (2, NULL), (3, 'c');"
        )
        conn.commit()
    finally:
        conn.close()
    return db


def _count(db: Path) -> int:
    conn = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True)
    try:
        return int(conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0])
    finally:
        conn.close()


def test_it_answers_from_the_live_path_and_says_which(live_db: Path) -> None:
    result = runner.invoke(app, ["db", "query", "select count(*) from jobs"])

    assert result.exit_code == 0, result.output
    assert result.stdout == "3\n", "stdout must carry the rows and nothing else"
    assert str(live_db) in result.stderr, "the command must say which file answered"
    assert "rows=1" in result.stderr


def test_rows_are_tab_separated_and_null_is_empty(live_db: Path) -> None:
    result = runner.invoke(app, ["db", "query", "select id, name from jobs order by id"])

    assert result.exit_code == 0, result.output
    assert result.stdout == "1\ta\n2\t\n3\tc\n"


def test_a_cell_never_breaks_the_row_format(live_db: Path) -> None:
    """A tab or newline inside TEXT would split one row into two; a BLOB would print
    as a Python literal."""
    result = runner.invoke(app, ["db", "query", "select 'a\tb', 'c\nd', 'e\\f', x'00ff'"])

    assert result.exit_code == 0, result.output
    assert result.stdout == "a\\tb\tc\\nd\te\\\\f\t00ff\n"


@pytest.mark.parametrize("sql", ["", "   ", "-- only a comment"])
def test_an_empty_statement_is_refused(live_db: Path, sql: str) -> None:
    # `--` ends option parsing, which is how a SQL comment has to be passed anyway.
    result = runner.invoke(app, ["db", "query", "--", sql])

    assert result.exit_code != 0, "an empty statement exited 0 and read as zero rows"
    assert result.stdout == ""
    assert "nothing to read" in result.stderr


def test_the_attachment_hint_is_only_for_attachments(live_db: Path) -> None:
    result = runner.invoke(app, ["db", "query", "select * from no_such_table"])

    assert result.exit_code == 1
    assert "ATTACH" not in result.stderr, "an unrelated error was blamed on attachments"


def test_a_reader_that_stops_reading_is_not_an_error(
    live_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`stackowl db query ... | head -1` closes the pipe after one row."""
    import typer

    real_echo = typer.echo

    def _closed_stdout(message: object = None, *args: object, err: bool = False, **kwargs: object) -> None:
        if not err:
            raise BrokenPipeError
        real_echo(message, *args, err=err, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(typer, "echo", _closed_stdout)
    result = runner.invoke(app, ["db", "query", "select id from jobs"])

    assert result.exit_code == 0, result.output
    assert not isinstance(result.exception, BrokenPipeError)


def test_a_write_is_refused_and_changes_nothing(live_db: Path) -> None:
    result = runner.invoke(app, ["db", "query", "delete from jobs"])

    assert result.exit_code != 0
    assert "read-only" in result.stderr.lower(), result.output
    assert _count(live_db) == 3


@pytest.mark.parametrize(
    "template", ["ATTACH DATABASE '{path}' AS born", "VACUUM INTO '{path}'"]
)
def test_a_query_cannot_create_another_database(
    live_db: Path, tmp_path: Path, template: str
) -> None:
    born = tmp_path / "born.db"

    result = runner.invoke(app, ["db", "query", template.format(path=born)])

    assert result.exit_code != 0, result.output
    assert not born.exists(), "a read-only query created a new database file"
    assert "ATTACH" in result.stderr, "the refusal must say why"


def test_a_missing_database_is_an_error_and_is_not_created(home: Path) -> None:
    db = StackowlHome.db_path()

    result = runner.invoke(app, ["db", "query", "select 1"])

    assert result.exit_code == 2, result.output
    assert str(db) in result.stderr, "the remedy must name the path it resolved"
    assert not db.exists(), "querying a missing database created it"
    assert not (home / "stackowl.db").exists()


def test_an_empty_database_is_an_error_not_a_zero(home: Path) -> None:
    db = StackowlHome.db_path()
    db.parent.mkdir(parents=True)
    db.touch()

    result = runner.invoke(app, ["db", "query", "select 1"])

    assert result.exit_code == 2, result.output
    assert "empty" in result.stderr.lower()
    assert result.stdout == ""
    assert db.stat().st_size == 0


def test_the_old_shell_helper_is_gone_and_nothing_points_at_it() -> None:
    name = "db_query" + ".sh"  # built, so this file is not a reference to it
    assert not (_ROOT / "scripts" / name).exists(), "the shell helper came back"

    hits: list[str] = []
    candidates = [_ROOT / "AGENTS.md", _ROOT / "README.md"]
    for base in ("docs", "scripts", "src"):
        candidates.extend((_ROOT / base).rglob("*"))
    for path in candidates:
        if not path.is_file() or path.suffix not in {".md", ".py", ".sh", ".toml", ".yaml"}:
            continue
        if name in path.read_text(encoding="utf-8", errors="ignore"):
            hits.append(str(path.relative_to(_ROOT)))
    assert not hits, f"still pointing at the deleted helper: {hits}"


def test_agents_are_told_where_the_database_is_outside_the_generated_block() -> None:
    """A `bmad-project-context` refresh replaces everything between its markers, so the
    runtime-state lines agents rely on must live outside them."""
    import re

    text = (_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    outside = re.sub(
        r"<!-- bmad:context -->.*?<!-- /bmad:context -->", "", text, flags=re.DOTALL
    )

    assert "stackowl db query" in outside
    assert "StackowlHome.db_path()" in outside
