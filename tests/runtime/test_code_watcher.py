"""CodeWatcher — quiet-period debounce over a watched *.py tree.

Drives ``_check_once`` directly with real files (explicit mtimes via os.utime)
and an injected clock, so settle timing is deterministic and fast — no sleeps,
no daemon thread.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

from stackowl.runtime.code_watcher import CodeWatcher


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def _write(path: Path, text: str, mtime: float) -> None:
    path.write_text(text)
    os.utime(path, (mtime, mtime))


def _make(tmp_path: Path, clock: FakeClock, quiet: float = 300.0) -> tuple[CodeWatcher, list[int]]:
    fired: list[int] = []
    src = tmp_path / "src"
    src.mkdir()
    _write(src / "a.py", "x = 1\n", mtime=500.0)
    _write(src / "b.py", "y = 2\n", mtime=500.0)
    watcher = CodeWatcher(
        [src],
        on_change=lambda: fired.append(1),
        quiet_period_s=quiet,
        clock=clock,
    )
    watcher._baseline = watcher._scan()  # establish applied baseline (as start() does)
    return watcher, fired


def test_no_change_never_fires(tmp_path: Path) -> None:
    clock = FakeClock()
    watcher, fired = _make(tmp_path, clock)
    for _ in range(5):
        clock.advance(1000)
        watcher._check_once()
    assert fired == []


def test_change_fires_only_after_quiet_period(tmp_path: Path) -> None:
    clock = FakeClock()
    watcher, fired = _make(tmp_path, clock, quiet=300.0)
    _write(tmp_path / "src" / "a.py", "x = 99\n", mtime=600.0)

    watcher._check_once()  # first sighting → pending, timer starts
    assert fired == []
    clock.advance(299)
    watcher._check_once()  # still within quiet window
    assert fired == []
    clock.advance(2)
    watcher._check_once()  # quiet period elapsed → fire
    assert fired == [1]


def test_burst_of_edits_coalesces_into_one_fire(tmp_path: Path) -> None:
    clock = FakeClock()
    watcher, fired = _make(tmp_path, clock, quiet=300.0)

    # Edit a.py, wait a bit, edit b.py again (resets the timer) — a save burst.
    _write(tmp_path / "src" / "a.py", "x = 7\n", mtime=600.0)
    watcher._check_once()
    clock.advance(200)
    _write(tmp_path / "src" / "b.py", "y = 8\n", mtime=700.0)
    watcher._check_once()  # signature changed again → timer resets
    clock.advance(200)
    watcher._check_once()  # only 200s since last change → no fire yet
    assert fired == []
    clock.advance(101)
    watcher._check_once()  # 301s quiet → single fire
    assert fired == [1]


def test_new_file_is_detected(tmp_path: Path) -> None:
    clock = FakeClock()
    watcher, fired = _make(tmp_path, clock, quiet=100.0)
    _write(tmp_path / "src" / "c.py", "z = 3\n", mtime=600.0)
    watcher._check_once()
    clock.advance(101)
    watcher._check_once()
    assert fired == [1]


def test_revert_before_settle_cancels_fire(tmp_path: Path) -> None:
    clock = FakeClock()
    watcher, fired = _make(tmp_path, clock, quiet=300.0)
    _write(tmp_path / "src" / "a.py", "x = 99\n", mtime=600.0)
    watcher._check_once()  # pending
    # Revert to the exact baseline content+mtime before the quiet window ends.
    _write(tmp_path / "src" / "a.py", "x = 1\n", mtime=500.0)
    clock.advance(400)
    watcher._check_once()  # back to baseline → pending cleared, no fire
    assert fired == []


def test_non_py_files_are_ignored(tmp_path: Path) -> None:
    clock = FakeClock()
    watcher, fired = _make(tmp_path, clock, quiet=100.0)
    _write(tmp_path / "src" / "notes.txt", "hello", mtime=600.0)
    watcher._check_once()
    clock.advance(101)
    watcher._check_once()
    assert fired == []


def test_second_change_after_a_fire_fires_again(tmp_path: Path) -> None:
    clock = FakeClock()
    watcher, fired = _make(tmp_path, clock, quiet=100.0)
    _write(tmp_path / "src" / "a.py", "x = 2\n", mtime=600.0)
    watcher._check_once()
    clock.advance(101)
    watcher._check_once()
    assert fired == [1]
    # A later, distinct change fires a second restart.
    _write(tmp_path / "src" / "a.py", "x = 3\n", mtime=700.0)
    watcher._check_once()
    clock.advance(101)
    watcher._check_once()
    assert fired == [1, 1]


def test_dispatch_without_loop_calls_back_directly(tmp_path: Path) -> None:
    clock = FakeClock()
    watcher, fired = _make(tmp_path, clock, quiet=0.0)
    # No loop captured → direct synchronous callback.
    _write(tmp_path / "src" / "a.py", "x = 5\n", mtime=600.0)
    watcher._check_once()  # pending, quiet=0
    watcher._check_once()  # settled immediately
    assert fired == [1]


async def test_threaded_run_dispatches_onto_loop(tmp_path: Path) -> None:
    """End-to-end: real daemon thread + cross-thread call_soon_threadsafe marshal."""
    src = tmp_path / "src"
    src.mkdir()
    _write(src / "a.py", "x = 1\n", mtime=500.0)
    fired = asyncio.Event()
    loop = asyncio.get_running_loop()

    def on_change() -> None:
        # Must run on the loop thread, not the watcher daemon thread.
        assert asyncio.get_running_loop() is loop
        fired.set()

    watcher = CodeWatcher(
        [src], on_change=on_change, poll_interval_s=0.02, quiet_period_s=0.0,
        clock=time.monotonic,
    )
    watcher.capture_loop(loop)
    watcher.start()
    try:
        _write(src / "a.py", "x = 2\n", mtime=600.0)
        await asyncio.wait_for(fired.wait(), timeout=5.0)
    finally:
        watcher.stop()
    assert fired.is_set()
