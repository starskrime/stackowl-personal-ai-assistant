"""OPS-2 (F147) — `init` does real work, not a "not yet implemented" stub.

`init` now initializes a StackOwl installation idempotently: it ensures the
~/.stackowl/ tree exists and applies pending migrations, so the DB is ready
without booting the server. `--help` must reflect a real command.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from stackowl.cli.app import app

runner = CliRunner()


def _setup_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "stackowl-home"
    monkeypatch.setenv("STACKOWL_HOME", str(home))
    for var in (
        "STACKOWL_DATA_DIR",
        "STACKOWL_LOG_DIR",
        "STACKOWL_PID_FILE",
        "STACKOWL_CONFIG_FILE",
    ):
        monkeypatch.delenv(var, raising=False)
    return home


def test_init_creates_home_and_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _setup_home(tmp_path, monkeypatch)

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0, result.output
    assert "not yet implemented" not in result.output
    assert home.exists()
    assert (home / "workspace").exists()
    # Migrations applied → the SQLite DB and the schema_migrations table exist.
    db = home / "workspace" / "stackowl.db"
    assert db.exists(), "init must apply migrations and create the database"
    import sqlite3

    conn = sqlite3.connect(db)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
        ).fetchall()
    finally:
        conn.close()
    assert rows, "schema_migrations table should exist after init"


def test_init_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup_home(tmp_path, monkeypatch)

    first = runner.invoke(app, ["init"])
    second = runner.invoke(app, ["init"])

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
