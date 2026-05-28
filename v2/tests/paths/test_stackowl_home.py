"""Tests for StackowlHome — path resolver with env-var override."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


def test_home_defaults_to_dot_stackowl_under_user_home(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STACKOWL_HOME", raising=False)
    from stackowl.paths import StackowlHome

    assert StackowlHome.home() == Path.home() / ".stackowl"


def test_home_honors_STACKOWL_HOME_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path / "myhome"))
    from stackowl.paths import StackowlHome

    assert StackowlHome.home() == tmp_path / "myhome"


def test_config_file_honors_STACKOWL_CONFIG_FILE_legacy_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "custom.yaml"
    monkeypatch.setenv("STACKOWL_CONFIG_FILE", str(cfg))
    monkeypatch.delenv("STACKOWL_HOME", raising=False)
    from stackowl.paths import StackowlHome

    assert StackowlHome.config_file() == cfg


def test_workspace_honors_STACKOWL_DATA_DIR_legacy_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = tmp_path / "ws"
    monkeypatch.setenv("STACKOWL_DATA_DIR", str(ws))
    monkeypatch.delenv("STACKOWL_HOME", raising=False)
    from stackowl.paths import StackowlHome

    assert StackowlHome.workspace() == ws


def test_logs_dir_honors_STACKOWL_LOG_DIR_legacy_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    logs = tmp_path / "logs"
    monkeypatch.setenv("STACKOWL_LOG_DIR", str(logs))
    monkeypatch.delenv("STACKOWL_HOME", raising=False)
    from stackowl.paths import StackowlHome

    assert StackowlHome.logs_dir() == logs


def test_ensure_exists_creates_full_tree_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "stackowl-home"
    monkeypatch.setenv("STACKOWL_HOME", str(home))
    monkeypatch.delenv("STACKOWL_DATA_DIR", raising=False)
    monkeypatch.delenv("STACKOWL_LOG_DIR", raising=False)
    monkeypatch.delenv("STACKOWL_PID_FILE", raising=False)
    from stackowl.paths import StackowlHome

    # First call — creates everything
    StackowlHome.ensure_exists()
    assert home.exists()
    assert (home / ".secrets").exists()
    assert (home / "workspace").exists()
    assert (home / "workspace" / "kuzu").exists()
    assert (home / "workspace" / "lancedb").exists()
    assert (home / "workspace" / "tools").exists()
    assert (home / "workspace" / "knowledge").exists()
    assert (home / "logs").exists()
    assert (home / "plugins").exists()
    assert (home / "runtime").exists()

    # Second call — idempotent, no error
    StackowlHome.ensure_exists()


@pytest.mark.skipif(sys.platform == "win32", reason="chmod not meaningful on Windows")
def test_ensure_exists_sets_secrets_dir_0700_on_posix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "stackowl-home"
    monkeypatch.setenv("STACKOWL_HOME", str(home))
    monkeypatch.delenv("STACKOWL_DATA_DIR", raising=False)
    monkeypatch.delenv("STACKOWL_LOG_DIR", raising=False)
    monkeypatch.delenv("STACKOWL_PID_FILE", raising=False)
    from stackowl.paths import StackowlHome

    StackowlHome.ensure_exists()
    secrets = home / ".secrets"
    mode = secrets.stat().st_mode & 0o777
    assert mode == 0o700, f"Expected 0o700, got 0o{mode:o}"
