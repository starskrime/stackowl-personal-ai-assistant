"""F040 — plugin install CLI: real local install (consent-gated), honest remote defer.

The CLI ``plugins install`` printed deferrals for BOTH local and remote sources.
Local install runs third-party Python code at ``serve`` boot, so it is gated behind
explicit interactive consent + a warning and fails closed off-TTY. Remote install
requires a verified (checksum/signature) index entry — the index schema has no such
field yet, so remote honestly exits non-zero (NOT a fake "installed", NOT auto-exec
of a download).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from stackowl.cli.app import _install_local_plugin
from stackowl.db.migrations.runner import MigrationRunner
from stackowl.exceptions import PluginValidationError
from stackowl.paths import StackowlHome


def _write_plugin(dir_: Path, name: str = "demo_plugin") -> Path:
    dir_.mkdir(parents=True, exist_ok=True)
    (dir_ / "plugin.yaml").write_text(
        "name: " + name + "\n"
        "version: 1.0.0\n"
        "type: local_plugin\n"
        "entry_point: demo_entry\n"
        "description: A demo plugin\n",
        encoding="utf-8",
    )
    return dir_


def _migrated_db(tmp_path: Path) -> Path:
    db = tmp_path / "stackowl.db"
    MigrationRunner(db_path=db).run()
    return db


def test_local_install_copies_and_registers_with_consent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = _write_plugin(tmp_path / "src")
    plugins_home = tmp_path / "home_plugins"
    monkeypatch.setattr(StackowlHome, "plugins_dir", classmethod(lambda cls: plugins_home))
    db = _migrated_db(tmp_path)

    name = _install_local_plugin(src, consent_granted=True, db_path=db)

    assert name == "demo_plugin"
    # Copied under ~/.stackowl/plugins/<name>/ (all-state-in-home mandate).
    assert (plugins_home / "demo_plugin" / "plugin.yaml").exists()
    # DB row recorded so `serve` re-hydrates it at boot.
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute(
            "SELECT name FROM plugins WHERE name = ?", ("demo_plugin",)
        ).fetchall()
    finally:
        conn.close()
    assert rows == [("demo_plugin",)]


def test_local_install_fails_closed_without_consent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = _write_plugin(tmp_path / "src")
    plugins_home = tmp_path / "home_plugins"
    monkeypatch.setattr(StackowlHome, "plugins_dir", classmethod(lambda cls: plugins_home))
    db = _migrated_db(tmp_path)

    with pytest.raises(PermissionError):
        _install_local_plugin(src, consent_granted=False, db_path=db)

    # Nothing copied, nothing registered — fail closed.
    assert not (plugins_home / "demo_plugin").exists()


def test_local_install_surfaces_validation_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "plugin.yaml").write_text("name: BadName!\nversion: notsemver\n", encoding="utf-8")
    monkeypatch.setattr(
        StackowlHome, "plugins_dir", classmethod(lambda cls: tmp_path / "h")
    )
    db = _migrated_db(tmp_path)

    with pytest.raises(PluginValidationError):
        _install_local_plugin(bad, consent_granted=True, db_path=db)


def test_local_install_missing_yaml_is_validation_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr(
        StackowlHome, "plugins_dir", classmethod(lambda cls: tmp_path / "h")
    )
    db = _migrated_db(tmp_path)

    with pytest.raises(PluginValidationError):
        _install_local_plugin(empty, consent_granted=True, db_path=db)
