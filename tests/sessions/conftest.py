"""Session-test defaults.

ESC-13 pins one thing here for every test in this directory.
"""

from __future__ import annotations

import datetime

import pytest


@pytest.fixture(autouse=True)
def _pin_the_process_start(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stop the restart trigger from firing on every fixture in this directory.

    ESC-13 rolls a lane's incarnation when it predates the running process, so a
    redeployed core cannot re-freeze a prompt under an id that already has one.
    These tests drive the store with FIXED clocks in the past (2026-07-20 and
    similar), and a real process always starts after those dates — so without
    this, every lane here looks like it outlived its process and rolls, and each
    test silently measures the restart trigger instead of the branch it was
    written for. Seven tests failed exactly that way.

    Pinned before the fixtures' own clocks. The trigger keeps its own coverage in
    test_esc13_restart_rolls_the_incarnation.py, which calls the pure policy
    directly and passes the timestamp explicitly.
    """
    monkeypatch.setattr(
        "stackowl.sessions.store._PROCESS_STARTED_AT",
        datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
        raising=False,
    )
