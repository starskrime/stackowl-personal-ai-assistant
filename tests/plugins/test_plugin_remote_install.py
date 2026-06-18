"""PLUG-2 — verified remote install routes through consent-gated local install.

A remote install: (1) downloads the archive to a temp dir via an INJECTED downloader
(mocked here — no real network), (2) verifies its sha256 against the index entry,
(3) ONLY on a verified match extracts and installs it via the SAME consent-gated
local path, which records the verified hash. A checksum mismatch refuses install and
copies/executes NOTHING. The download is never executed — only hashed + unzipped.
"""

from __future__ import annotations

import hashlib
import sqlite3
import zipfile
from pathlib import Path

import pytest

from stackowl.db.migrations.runner import MigrationRunner
from stackowl.paths import StackowlHome
from stackowl.plugins.index import PluginIndexEntry
from stackowl.plugins.verify import PluginVerificationError
from stackowl.plugins.remote_install import install_remote_plugin


def _make_plugin_zip(tmp: Path) -> bytes:
    """Build a zip whose top-level dir holds a valid plugin.yaml."""
    src = tmp / "build" / "demo_plugin"
    src.mkdir(parents=True)
    (src / "plugin.yaml").write_text(
        "name: demo_plugin\nversion: 1.0.0\ntype: local_plugin\n"
        "entry_point: demo_entry\ndescription: A demo plugin\n",
        encoding="utf-8",
    )
    zpath = tmp / "demo_plugin.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.write(src / "plugin.yaml", arcname="demo_plugin/plugin.yaml")
    return zpath.read_bytes()


def _migrated_db(tmp_path: Path) -> Path:
    db = tmp_path / "stackowl.db"
    MigrationRunner(db_path=db).run()
    return db


def test_plugins_table_has_sha256_column(tmp_path: Path) -> None:
    db = _migrated_db(tmp_path)
    conn = sqlite3.connect(db)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(plugins)").fetchall()}
    finally:
        conn.close()
    assert "sha256" in cols


def test_verified_remote_install_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    blob = _make_plugin_zip(tmp_path)
    digest = hashlib.sha256(blob).hexdigest()
    entry = PluginIndexEntry(
        name="demo_plugin", url="https://e/demo_plugin.zip", version="1.0.0",
        description="d", type="local_plugin", sha256=digest,
    )
    plugins_home = tmp_path / "home_plugins"
    monkeypatch.setattr(StackowlHome, "plugins_dir", classmethod(lambda cls: plugins_home))
    db = _migrated_db(tmp_path)

    name = install_remote_plugin(
        entry, consent_granted=True, db_path=db,
        downloader=lambda _url: blob,  # injected — no network, never executed
    )
    assert name == "demo_plugin"
    assert (plugins_home / "demo_plugin" / "plugin.yaml").exists()
    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT sha256 FROM plugins WHERE name = ?", ("demo_plugin",)
        ).fetchone()
    finally:
        conn.close()
    assert row is not None and row[0] == digest


def test_tampered_remote_install_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    blob = _make_plugin_zip(tmp_path)
    entry = PluginIndexEntry(
        name="demo_plugin", url="https://e/demo_plugin.zip", version="1.0.0",
        description="d", type="local_plugin", sha256="0" * 64,  # WRONG digest
    )
    plugins_home = tmp_path / "home_plugins"
    monkeypatch.setattr(StackowlHome, "plugins_dir", classmethod(lambda cls: plugins_home))
    db = _migrated_db(tmp_path)

    with pytest.raises(PluginVerificationError):
        install_remote_plugin(
            entry, consent_granted=True, db_path=db, downloader=lambda _url: blob,
        )
    # NOTHING installed — fail closed.
    assert not (plugins_home / "demo_plugin").exists()


def test_unsigned_entry_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    blob = _make_plugin_zip(tmp_path)
    entry = PluginIndexEntry(
        name="demo_plugin", url="https://e/demo_plugin.zip", version="1.0.0",
        description="d", type="local_plugin", sha256="",  # no digest → unverifiable
    )
    monkeypatch.setattr(StackowlHome, "plugins_dir", classmethod(lambda cls: tmp_path / "h"))
    db = _migrated_db(tmp_path)
    with pytest.raises(PluginVerificationError):
        install_remote_plugin(
            entry, consent_granted=True, db_path=db, downloader=lambda _url: blob,
        )
