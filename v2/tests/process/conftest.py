"""Shared fixtures for the process-substrate tests (E9-S0).

A hand-advanced fake monotonic clock (ARCH-99) drives every TTL/deadline
deterministically — no sleeping. ``STACKOWL_HOME`` is pointed at a tmp dir so the
checkpoint never touches the real ``~/.stackowl/`` ([[feedback_all_state_in_home]]).
All spawned subprocesses use ``sys.executable -c "..."`` so the suite runs
identically on Windows and POSIX ([[feedback_cross_platform]]).
"""

from __future__ import annotations

import sys

import pytest


class FakeClock:
    """Injectable clock with a hand-advanced monotonic time (ARCH-99)."""

    def __init__(self, start: float = 1000.0) -> None:
        self._t = start

    def monotonic(self) -> float:
        return self._t

    def advance(self, seconds: float) -> None:
        self._t += seconds

    async def async_sleep(self, seconds: float) -> None:  # pragma: no cover — unused
        return None


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch) -> None:
    """Point all ~/.stackowl/ state at a tmp dir for every test (autouse)."""
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path / "home"))


def py(code: str) -> list[str]:
    """A cross-platform argv running ``code`` via the test interpreter."""
    return [sys.executable, "-u", "-c", code]
