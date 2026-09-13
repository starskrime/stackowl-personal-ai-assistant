"""`stackowl db backup` and `db restore` refuse a missing source instead of creating one.

MEASURED 2026-09-12: both opened their source with a bare `sqlite3.connect`, which
creates a missing file. `db backup` against a mistyped home created an empty live
database and backed it up; `db restore` given a mistyped path created that file, ran
`integrity_check` on it, was told "ok", and offered to replace the live database with
nothing.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from stackowl.cli.app import app
from stackowl.config.test_mode import TestModeGuard
from stackowl.paths import StackowlHome

runner = CliRunner()


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "home"
    monkeypatch.setenv("STACKOWL_HOME", str(root))
    monkeypatch.delenv("STACKOWL_DATA_DIR", raising=False)
    # Both commands refuse to run in test mode; these cases never reach live I/O.
    monkeypatch.setattr(TestModeGuard, "_active", False)
    return root


def test_backup_refuses_a_missing_database_and_creates_nothing(home: Path, tmp_path: Path) -> None:
    out = tmp_path / "out" / "backup.db"

    result = runner.invoke(app, ["db", "backup", str(out)])

    assert result.exit_code == 1, result.output
    assert str(StackowlHome.db_path()) in result.stderr
    assert not StackowlHome.db_path().exists(), "the backup created the live database"
    assert not out.exists()


def test_backup_of_a_live_database_still_works(home: Path, tmp_path: Path) -> None:
    """The source now opens read-only; VACUUM INTO must still write the output."""
    db = StackowlHome.db_path()
    db.parent.mkdir(parents=True)
    conn = sqlite3.connect(db)
    try:
        conn.execute("CREATE TABLE t (x INTEGER)")
        conn.execute("INSERT INTO t VALUES (7)")
        conn.commit()
    finally:
        conn.close()
    out = tmp_path / "backup.db"

    result = runner.invoke(app, ["db", "backup", str(out)])

    assert result.exit_code == 0, result.output
    check = sqlite3.connect(f"{out.resolve().as_uri()}?mode=ro", uri=True)
    try:
        assert check.execute("SELECT x FROM t").fetchall() == [(7,)]
    finally:
        check.close()


def test_restore_refuses_a_missing_file_and_does_not_create_it(home: Path, tmp_path: Path) -> None:
    missing = tmp_path / "nope.db"

    result = runner.invoke(app, ["db", "restore", str(missing)])

    assert result.exit_code == 1, result.output
    assert "missing or empty" in result.stderr
    assert not missing.exists(), "the restore created the file it was asked to restore"


def test_restore_refuses_an_empty_file(home: Path, tmp_path: Path) -> None:
    empty = tmp_path / "empty.db"
    empty.touch()

    result = runner.invoke(app, ["db", "restore", str(empty)])

    assert result.exit_code == 1, result.output
    assert "missing or empty" in result.stderr
    assert empty.stat().st_size == 0
