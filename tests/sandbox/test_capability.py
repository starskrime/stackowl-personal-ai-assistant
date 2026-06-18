"""Tests for SandboxCapability.probe — structured, never raises, mocked host."""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from typing import Any

import pytest

from stackowl.sandbox.capability import SandboxCapability, SandboxProbe


def _which(present: set[str]):
    def _inner(name: str) -> str | None:
        return f"/usr/bin/{name}" if name in present else None

    return _inner


def _run_ok(_argv: list[str], **_kw: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(_argv, returncode=0, stdout="1.2.3\n", stderr="")


def _run_fail(_argv: list[str], **_kw: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(_argv, returncode=1, stdout="", stderr="nope")


@pytest.fixture
def force_linux(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr("stackowl.sandbox.capability.platform.system", lambda: "Linux")
    yield


class TestProbe:
    def test_bwrap_present_docker_present(
        self, monkeypatch: pytest.MonkeyPatch, force_linux: None
    ) -> None:
        monkeypatch.setattr(
            "stackowl.sandbox.capability.shutil.which", _which({"bwrap", "docker"})
        )
        monkeypatch.setattr("stackowl.sandbox.capability.subprocess.run", _run_ok)
        probe = SandboxCapability.probe()
        assert probe.bwrap_viable is True
        assert probe.docker_viable is True
        assert probe.any_viable is True
        assert probe.platform_supported is True

    def test_bwrap_only(self, monkeypatch: pytest.MonkeyPatch, force_linux: None) -> None:
        monkeypatch.setattr("stackowl.sandbox.capability.shutil.which", _which({"bwrap"}))
        monkeypatch.setattr("stackowl.sandbox.capability.subprocess.run", _run_ok)
        probe = SandboxCapability.probe()
        assert probe.bwrap_viable is True
        assert probe.docker_viable is False
        assert "not installed" in probe.docker_reason

    def test_docker_only(self, monkeypatch: pytest.MonkeyPatch, force_linux: None) -> None:
        monkeypatch.setattr("stackowl.sandbox.capability.shutil.which", _which({"docker"}))
        monkeypatch.setattr("stackowl.sandbox.capability.subprocess.run", _run_ok)
        probe = SandboxCapability.probe()
        assert probe.bwrap_viable is False
        assert probe.docker_viable is True

    def test_neither(self, monkeypatch: pytest.MonkeyPatch, force_linux: None) -> None:
        monkeypatch.setattr("stackowl.sandbox.capability.shutil.which", _which(set()))
        probe = SandboxCapability.probe()
        assert probe.any_viable is False
        assert probe.bwrap_viable is False
        assert probe.docker_viable is False

    def test_present_but_version_fails_is_not_viable(
        self, monkeypatch: pytest.MonkeyPatch, force_linux: None
    ) -> None:
        # Binary on PATH but its probe exits non-zero (broken bwrap / dead daemon).
        monkeypatch.setattr(
            "stackowl.sandbox.capability.shutil.which", _which({"bwrap", "docker"})
        )
        monkeypatch.setattr("stackowl.sandbox.capability.subprocess.run", _run_fail)
        probe = SandboxCapability.probe()
        assert probe.bwrap_viable is False
        assert probe.docker_viable is False

    def test_subprocess_timeout_is_not_viable(
        self, monkeypatch: pytest.MonkeyPatch, force_linux: None
    ) -> None:
        def _timeout(_argv: list[str], **_kw: Any) -> subprocess.CompletedProcess[str]:
            raise subprocess.TimeoutExpired(cmd=_argv, timeout=5.0)

        monkeypatch.setattr("stackowl.sandbox.capability.shutil.which", _which({"docker"}))
        monkeypatch.setattr("stackowl.sandbox.capability.subprocess.run", _timeout)
        probe = SandboxCapability.probe()
        assert probe.docker_viable is False  # timeout → not viable, no raise.

    def test_non_linux_host_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("stackowl.sandbox.capability.platform.system", lambda: "Darwin")
        probe = SandboxCapability.probe()
        assert probe.any_viable is False
        assert probe.platform_supported is False
        assert "Linux" in probe.bwrap_reason

    def test_probe_never_raises_on_internal_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _boom() -> str:
            raise RuntimeError("kaboom")

        monkeypatch.setattr("stackowl.sandbox.capability.platform.system", _boom)
        probe = SandboxCapability.probe()  # must not raise.
        assert isinstance(probe, SandboxProbe)
        assert probe.any_viable is False
        assert probe.platform_supported is False
