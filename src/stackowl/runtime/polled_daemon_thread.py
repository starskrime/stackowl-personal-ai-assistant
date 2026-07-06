"""PolledDaemonThread — shared daemon-thread poll-loop lifecycle.

ConfigWatcher (config/watcher.py) and CodeWatcher (runtime/code_watcher.py)
independently implemented the identical thread lifecycle (capture_loop/start/
stop/loop-body: sleep(poll_interval), check_once()) around genuinely different
debounce semantics (config settles on 2 consecutive identical polls; code
settles on a wall-clock quiet period). This owns ONLY the identical part —
each subclass still owns its own scan/debounce/dispatch logic, which tonyStyle
found are real, independently-tested contracts, not accidental duplication.
"""

from __future__ import annotations

import asyncio
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable


class PolledDaemonThread(ABC):
    """Base for a daemon thread that polls on an interval until stopped."""

    def __init__(self, *, poll_interval_s: float, thread_name: str) -> None:
        self._poll_interval = poll_interval_s
        self._thread_name = thread_name
        self._thread: threading.Thread | None = None
        self._running = False
        self._loop: asyncio.AbstractEventLoop | None = None

    def capture_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Record the asyncio loop to marshal a dispatched callback onto."""
        self._loop = loop

    def start(self) -> None:
        self._running = True
        self._on_start()
        if self._loop is None:
            try:
                self._loop = asyncio.get_running_loop()
            except RuntimeError:
                self._loop = None  # no loop (e.g. a sync test) → direct dispatch
        self._thread = threading.Thread(
            target=self._loop_body, daemon=True, name=self._thread_name
        )
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None

    def _loop_body(self) -> None:
        while self._running:
            time.sleep(self._poll_interval)
            self._check_once()

    def _dispatch(self, callback: Callable[..., object], *args: object) -> None:
        """Invoke ``callback`` on the captured loop, never the watcher thread."""
        loop = self._loop
        if loop is None or not loop.is_running():
            callback(*args)
            return
        loop.call_soon_threadsafe(callback, *args)

    @abstractmethod
    def _on_start(self) -> None:
        """Establish the baseline signature (called on the caller's thread)."""

    @abstractmethod
    def _check_once(self) -> None:
        """One poll tick: scan, debounce, dispatch on settle. Subclass-owned."""
