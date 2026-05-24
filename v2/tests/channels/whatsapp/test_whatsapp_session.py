"""Tests for WhatsAppSessionManager — Story 9.8."""

from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path

import pytest

from stackowl.channels.whatsapp.session import WhatsAppSessionManager


@pytest.fixture()
def tmp_session_dir(tmp_path: Path) -> str:
    """Provide a temporary directory for each test."""
    return str(tmp_path / "whatsapp_session")


def test_session_file_path_returns_expected_path(tmp_session_dir: str) -> None:
    """session_file_path() returns <session_dir>/state.json."""
    manager = WhatsAppSessionManager(tmp_session_dir)
    expected = Path(tmp_session_dir) / "state.json"
    assert manager.session_file_path() == expected


def test_save_writes_json_file(tmp_session_dir: str) -> None:
    """save() creates the session file with valid JSON content."""
    manager = WhatsAppSessionManager(tmp_session_dir)
    state = {"cookies": [{"name": "wa", "value": "abc"}], "origins": []}
    manager.save(state)

    path = manager.session_file_path()
    assert path.exists()
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["cookies"][0]["name"] == "wa"


def test_save_sets_restrictive_file_permissions(tmp_session_dir: str) -> None:
    """save() sets file permissions to 0o600 (owner read/write only)."""
    manager = WhatsAppSessionManager(tmp_session_dir)
    manager.save({"cookies": [], "origins": []})

    path = manager.session_file_path()
    file_mode = stat.S_IMODE(os.stat(path).st_mode)
    assert file_mode == 0o600


def test_load_returns_none_when_file_does_not_exist(tmp_session_dir: str) -> None:
    """load() returns None when no session file exists."""
    manager = WhatsAppSessionManager(tmp_session_dir)
    result = manager.load()
    assert result is None


def test_load_returns_dict_when_file_exists(tmp_session_dir: str) -> None:
    """load() returns the stored dict when the session file exists."""
    manager = WhatsAppSessionManager(tmp_session_dir)
    state = {"cookies": [{"name": "session_key", "value": "xyz"}], "origins": []}
    manager.save(state)

    loaded = manager.load()
    assert loaded is not None
    assert loaded["cookies"][0]["value"] == "xyz"


def test_exists_returns_false_when_no_file(tmp_session_dir: str) -> None:
    """exists() returns False when the session file has not been created."""
    manager = WhatsAppSessionManager(tmp_session_dir)
    assert manager.exists() is False


def test_exists_returns_true_when_file_exists(tmp_session_dir: str) -> None:
    """exists() returns True after a session has been saved."""
    manager = WhatsAppSessionManager(tmp_session_dir)
    manager.save({"cookies": [], "origins": []})
    assert manager.exists() is True


def test_clear_deletes_session_file(tmp_session_dir: str) -> None:
    """clear() removes the session file."""
    manager = WhatsAppSessionManager(tmp_session_dir)
    manager.save({"cookies": [], "origins": []})
    assert manager.exists() is True

    manager.clear()
    assert manager.exists() is False


def test_clear_no_error_when_no_file(tmp_session_dir: str) -> None:
    """clear() is safe to call when no session file exists."""
    manager = WhatsAppSessionManager(tmp_session_dir)
    # Should not raise.
    manager.clear()
