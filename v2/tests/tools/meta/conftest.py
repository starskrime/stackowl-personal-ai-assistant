"""Fixtures for the meta-tool (tool_build) tests: tmp home + live-IO guard."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest

from stackowl.config.test_mode import TestModeGuard


@pytest.fixture()
def tmp_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point StackowlHome at an isolated tmp root for the duration of a test.

    Clears the per-path legacy overrides so every sub-path (including
    workspace()/learned_tools_dir()) derives from this fresh root.
    """
    home = tmp_path / "home"
    monkeypatch.setenv("STACKOWL_HOME", str(home))
    monkeypatch.delenv("STACKOWL_DATA_DIR", raising=False)
    monkeypatch.delenv("STACKOWL_CONFIG_FILE", raising=False)
    monkeypatch.delenv("STACKOWL_LOG_DIR", raising=False)
    monkeypatch.delenv("STACKOWL_PID_FILE", raising=False)
    home.mkdir(parents=True, exist_ok=True)
    return home


@pytest.fixture()
def _live_io() -> Generator[None]:
    """Disable the TestModeGuard so tools may spawn real subprocesses."""
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]
    try:
        yield
    finally:
        TestModeGuard._active = prev  # type: ignore[attr-defined]
