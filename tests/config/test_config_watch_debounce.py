"""CFG-2 (F016) — watch poll default ≥5s + debounce re-stats a mid-write file.

Config edits are rare, so the default poll interval must be ≥5s (no 1Hz idle
wakeup). A change is debounced: the watcher re-stats after a short settle delay
and only reloads when the file has STOPPED changing, so a mid-write truncated
file is never parsed.
"""

from __future__ import annotations

from pathlib import Path

from stackowl.config.watcher import ConfigWatcher
from stackowl.events.bus import EventBus


def test_default_poll_interval_is_at_least_5s(tmp_path: Path) -> None:
    w = ConfigWatcher(tmp_path / "c.yaml", EventBus(), lambda: object())
    assert w._poll_interval >= 5.0


def test_change_settles_before_reload(tmp_path: Path) -> None:
    # Two consecutive _check_once calls where the mtime keeps changing must NOT
    # reload (the file is still being written); a reload only fires once the
    # mtime has settled (unchanged across the debounce window).
    import os

    cfg = tmp_path / "c.yaml"
    cfg.write_text("a: 1\n")
    os.utime(cfg, (100, 100))
    reloads: list[object] = []
    bus = EventBus()
    bus.subscribe("settings_reloaded", reloads.append)
    w = ConfigWatcher(cfg, bus, lambda: object(), poll_interval=5.0)
    w._last_mtime = w._mtime()

    # First sighting of a NEW mtime → mark pending, do NOT reload yet.
    os.utime(cfg, (200, 200))
    w._check_once()
    assert reloads == [], "reloaded mid-write before the change settled"

    # mtime unchanged on the next check → settled → reload fires exactly once.
    w._check_once()
    assert len(reloads) == 1


def test_continuous_writes_never_reload_until_settled(tmp_path: Path) -> None:
    cfg = tmp_path / "c.yaml"
    cfg.write_text("x: 0\n")
    reloads: list[object] = []
    bus = EventBus()
    bus.subscribe("settings_reloaded", reloads.append)
    w = ConfigWatcher(cfg, bus, lambda: object(), poll_interval=5.0)
    w._last_mtime = w._mtime()

    # Simulate three checks each catching a still-changing file → no reload.
    import os

    for i in range(3):
        os.utime(cfg, (100 + i, 100 + i))
        w._check_once()
    assert reloads == []

    # Now it settles → one reload.
    w._check_once()
    assert len(reloads) == 1
