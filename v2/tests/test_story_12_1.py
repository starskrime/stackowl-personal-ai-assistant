"""Story 12.1 — OS service integration tests.

Covers: PidManager, WatchdogService, KeepAliveService, and deploy file presence.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DEPLOY_DIR = Path(__file__).resolve().parent.parent / "deploy"


# ---------------------------------------------------------------------------
# PidManager tests
# ---------------------------------------------------------------------------


class TestPidManagerAcquireCreatesFile:
    """PidManager.acquire() creates a PID file at the expected path."""

    def test_acquire_creates_pid_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """After acquire(), a readable file containing the current PID must exist."""
        from stackowl.service.pid_manager import PidManager

        manager = PidManager()

        # Redirect pid_path to tmp_path so we don't pollute the real runtime dir
        pid_file = tmp_path / "stackowl.pid"
        monkeypatch.setattr(type(manager), "pid_path", property(lambda self: pid_file))

        manager.acquire()

        assert pid_file.exists(), "PID file was not created"
        content = pid_file.read_text(encoding="utf-8").strip()
        assert content == str(os.getpid()), f"Expected PID {os.getpid()}, got {content!r}"

    def test_acquire_creates_directory_if_missing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """acquire() succeeds even if the parent directory doesn't yet exist."""
        from stackowl.service.pid_manager import PidManager

        manager = PidManager()
        nested = tmp_path / "sub" / "dir"
        pid_file = nested / "stackowl.pid"
        nested.mkdir(parents=True)
        monkeypatch.setattr(type(manager), "pid_path", property(lambda self: pid_file))

        manager.acquire()
        assert pid_file.exists()


class TestPidManagerRaisesOnLiveProcess:
    """PidManager.acquire() raises PidFileExistsError when the PID is alive."""

    def test_raises_pid_file_exists_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from stackowl.exceptions import PidFileExistsError
        from stackowl.service.pid_manager import PidManager

        manager = PidManager()
        pid_file = tmp_path / "stackowl.pid"
        monkeypatch.setattr(type(manager), "pid_path", property(lambda self: pid_file))

        # Write a PID that is guaranteed to be alive — our own PID
        live_pid = os.getpid()
        pid_file.write_text(str(live_pid), encoding="utf-8")

        with pytest.raises(PidFileExistsError) as exc_info:
            manager.acquire()

        assert exc_info.value.pid == live_pid

    def test_overwrites_stale_pid_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """acquire() silently overwrites a PID file for a dead process."""
        from stackowl.service.pid_manager import PidManager

        manager = PidManager()
        pid_file = tmp_path / "stackowl.pid"
        monkeypatch.setattr(type(manager), "pid_path", property(lambda self: pid_file))

        # PID 0 is never a valid user process; os.kill(0, 0) on Unix checks the process group —
        # use a very high number that is almost certainly dead.
        stale_pid = 999_999_999
        pid_file.write_text(str(stale_pid), encoding="utf-8")

        # Should not raise — overwrite with current PID
        manager.acquire()
        content = pid_file.read_text(encoding="utf-8").strip()
        assert content == str(os.getpid())


class TestPidManagerRelease:
    """PidManager.release() removes the PID file."""

    def test_release_removes_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from stackowl.service.pid_manager import PidManager

        manager = PidManager()
        pid_file = tmp_path / "stackowl.pid"
        monkeypatch.setattr(type(manager), "pid_path", property(lambda self: pid_file))

        manager.acquire()
        assert pid_file.exists()

        manager.release()
        assert not pid_file.exists()

    def test_release_is_idempotent(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """release() does not raise when the PID file is already gone."""
        from stackowl.service.pid_manager import PidManager

        manager = PidManager()
        pid_file = tmp_path / "stackowl.pid"
        monkeypatch.setattr(type(manager), "pid_path", property(lambda self: pid_file))

        # Call release without ever calling acquire — must not raise
        manager.release()


# ---------------------------------------------------------------------------
# WatchdogService tests
# ---------------------------------------------------------------------------


class TestWatchdogServiceNoWatchdogUsec:
    """WatchdogService.start() logs and returns immediately without WATCHDOG_USEC."""

    def test_start_without_env_var_is_noop(self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
        import logging

        from stackowl.service.watchdog import WatchdogService

        monkeypatch.delenv("WATCHDOG_USEC", raising=False)

        with caplog.at_level(logging.INFO, logger="stackowl.infra"):
            svc = WatchdogService()
            svc.start()

        assert svc._task is None, "No asyncio task should be scheduled without WATCHDOG_USEC"
        assert any("not configured" in r.message or "skipping" in r.message for r in caplog.records), (
            "Expected a 'not configured' or 'skipping' log entry"
        )


# ---------------------------------------------------------------------------
# KeepAliveService tests
# ---------------------------------------------------------------------------


class TestKeepAliveServiceNonMacos:
    """KeepAliveService.start() logs and returns immediately on non-macOS."""

    def test_start_non_macos_is_noop(self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
        import logging

        from stackowl.service.watchdog import KeepAliveService

        # Force non-macOS platform
        monkeypatch.setattr(sys, "platform", "linux")

        with caplog.at_level(logging.INFO, logger="stackowl.infra"):
            svc = KeepAliveService()
            svc.start()

        assert any("not configured" in r.message or "skipping" in r.message for r in caplog.records), (
            "Expected a 'not configured' or 'skipping' log entry on non-macOS"
        )


# ---------------------------------------------------------------------------
# Deploy file content tests
# ---------------------------------------------------------------------------


class TestSystemdUnitFile:
    """deploy/stackowl.service contains required systemd directives."""

    @pytest.fixture(scope="class")
    def unit_content(self) -> str:
        path = _DEPLOY_DIR / "stackowl.service"
        assert path.exists(), f"Missing file: {path}"
        return path.read_text(encoding="utf-8")

    def test_type_notify(self, unit_content: str) -> None:
        assert "Type=notify" in unit_content

    def test_watchdog_sec(self, unit_content: str) -> None:
        assert "WatchdogSec=60" in unit_content

    def test_restart_on_failure(self, unit_content: str) -> None:
        assert "Restart=on-failure" in unit_content

    def test_user_stackowl(self, unit_content: str) -> None:
        assert "User=stackowl" in unit_content

    def test_wanted_by_multi_user(self, unit_content: str) -> None:
        assert "WantedBy=multi-user.target" in unit_content


class TestLaunchdPlist:
    """deploy/com.stackowl.plist contains required launchd keys."""

    @pytest.fixture(scope="class")
    def plist_content(self) -> str:
        path = _DEPLOY_DIR / "com.stackowl.plist"
        assert path.exists(), f"Missing file: {path}"
        return path.read_text(encoding="utf-8")

    def test_keep_alive_key(self, plist_content: str) -> None:
        assert "KeepAlive" in plist_content

    def test_run_at_load_key(self, plist_content: str) -> None:
        assert "RunAtLoad" in plist_content

    def test_stdout_log_path(self, plist_content: str) -> None:
        assert "~/Library/Logs/StackOwl/stdout.log" in plist_content


class TestPowerShellInstaller:
    """deploy/install-service.ps1 exists and references the StackOwl service name."""

    @pytest.fixture(scope="class")
    def ps1_content(self) -> str:
        path = _DEPLOY_DIR / "install-service.ps1"
        assert path.exists(), f"Missing file: {path}"
        return path.read_text(encoding="utf-8")

    def test_service_name_present(self, ps1_content: str) -> None:
        assert "StackOwl" in ps1_content

    def test_nssm_referenced(self, ps1_content: str) -> None:
        assert "nssm" in ps1_content.lower()
