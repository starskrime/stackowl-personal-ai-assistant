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


class ConfigWatcher:
    """Polls a YAML config file for modifications and re-validates on change.

    When the file changes and parses cleanly, emits ``settings_reloaded``
    on the provided ``EventBus`` with the new ``Settings`` object as payload.
    Invalid files are rejected with a WARNING; the previous settings are kept.

    Wired to start only when ``Settings.settings_watch`` is ``True``.
    """

    def __init__(
        self,
        config_path: Path,
        event_bus: EventBus,
        settings_factory: SettingsFactory,
        poll_interval: float = 1.0,
    ) -> None:
        self._path = config_path
        self._event_bus = event_bus
        self._settings_factory = settings_factory
        self._poll_interval = poll_interval
        self._last_mtime: float | None = None
        self._thread: threading.Thread | None = None
        self._running = False

    def start(self) -> None:
        self._running = True
        self._last_mtime = self._mtime()
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
            current = self._mtime()
            if current != self._last_mtime:
                self._last_mtime = current
                self._reload()

    def _reload(self) -> None:
        try:
            new_settings = self._settings_factory()
            self._event_bus.emit("settings_reloaded", new_settings)
            log.info("[config] Settings reloaded successfully")
        except Exception as exc:
            log.warning("[config] reload rejected: %s", exc)
