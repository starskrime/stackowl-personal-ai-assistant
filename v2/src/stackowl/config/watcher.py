"""ConfigWatcher — polls stackowl.yaml for changes and emits settings_reloaded."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from stackowl.events.bus import EventBus

log = logging.getLogger("stackowl.config")

SettingsFactory = Callable[[], Any]


# Config edits are RARE, so the default poll cadence is deliberately slow — a
# 1Hz stat() loop for the whole process lifetime is pure waste (CFG-2 / F016).
_DEFAULT_POLL_INTERVAL = 5.0


class ConfigWatcher:
    """Polls a YAML config file for modifications and re-validates on change.

    When the file changes and parses cleanly, emits ``settings_reloaded`` on the
    provided ``EventBus`` with the new ``Settings`` object as payload. A broken
    config REJECTS the reload and keeps the previous settings (CFG-1).

    Debounce (CFG-2 / F016): a detected change is not reloaded immediately. The
    watcher re-stats on the next poll and only reloads once the mtime has SETTLED
    (unchanged across one poll window), so a mid-write / truncated file is never
    parsed. The default poll interval is ``5s`` (config edits are rare → no 1Hz
    idle wakeup). The poll loop is the portable, cross-platform fallback; an
    inotify/watchdog observer can layer on top without changing this contract.

    Wired to start only when ``Settings.settings_watch`` is ``True``.
    """

    def __init__(
        self,
        config_path: Path,
        event_bus: EventBus,
        settings_factory: SettingsFactory,
        poll_interval: float = _DEFAULT_POLL_INTERVAL,
    ) -> None:
        self._path = config_path
        self._event_bus = event_bus
        self._settings_factory = settings_factory
        self._poll_interval = poll_interval
        self._last_mtime: float | None = None
        # Debounce state: the mtime first seen for an in-progress change, awaiting
        # a settle confirmation on the next poll. ``None`` = no change pending.
        self._pending_mtime: float | None = None
        self._thread: threading.Thread | None = None
        self._running = False

    def start(self) -> None:
        self._running = True
        self._last_mtime = self._mtime()
        self._pending_mtime = None
        self._thread = threading.Thread(target=self._loop, daemon=True, name="config-watcher")
        self._thread.start()
        log.info("[config] Watching %s for changes (poll %.1fs)", self._path, self._poll_interval)

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None

    def _mtime(self) -> float | None:
        try:
            return self._path.stat().st_mtime
        except OSError:
            return None

    def _loop(self) -> None:
        while self._running:
            time.sleep(self._poll_interval)
            self._check_once()

    def _check_once(self) -> None:
        """One poll tick: detect → debounce-settle → reload (CFG-2 / F016).

        A newly-changed mtime is recorded as *pending* and NOT reloaded yet. Only
        when the next tick sees the SAME mtime (the write has settled) does the
        reload fire — a still-changing (mid-write/truncated) file keeps deferring.
        """
        current = self._mtime()
        if current == self._last_mtime:
            # No change vs the last applied state — clear any stale pending mark.
            self._pending_mtime = None
            return
        if current != self._pending_mtime:
            # First sighting of this change (or it changed again since last tick)
            # → mark pending and wait for it to settle before reloading.
            log.debug(
                "[config] change detected, awaiting settle (debounce): %s", self._path
            )
            self._pending_mtime = current
            return
        # Settled: the mtime is unchanged across a full poll window → safe reload.
        self._last_mtime = current
        self._pending_mtime = None
        self._reload()

    def _reload(self) -> None:
        try:
            new_settings = self._settings_factory()
        except Exception as exc:
            # CFG-1 (F017) — a broken config (e.g. YAML parse error of an existing
            # file, now raised by _YamlSource) REJECTS the reload: the prior
            # Settings are retained (no settings_reloaded is emitted, so no
            # consumer mutates), and the operator is alerted at ERROR (health-
            # visible), NOT a buried WARNING.
            log.error(
                "[config] reload REJECTED — keeping previous settings: %s",
                exc,
                exc_info=exc,
            )
            return
        self._event_bus.emit("settings_reloaded", new_settings)
        log.info("[config] Settings reloaded successfully")
