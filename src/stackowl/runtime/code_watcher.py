"""CodeWatcher — polls the source tree and fires a restart after a quiet period.

Models :class:`stackowl.config.watcher.ConfigWatcher` (daemon-thread mtime poll,
cross-thread dispatch via ``call_soon_threadsafe``) but differs in two ways the
dev hot-restart needs:

* It watches a *tree* of ``*.py`` files under ``watch_paths`` (an aggregate
  signature of (path, mtime)), not a single config file.
* Its debounce is a **quiet-period** settle, not a one-tick settle: a detected
  change is held until ``quiet_period_s`` elapses with NO further change, so a
  burst of saves across many files coalesces into exactly one restart. (The
  user-facing knob is ``auto_restart.delay_minutes``.)

The watcher is transport-agnostic: on settle it invokes ``on_change()`` (a plain
callback) marshalled onto the captured asyncio loop. The callback owns the
policy (is a client connected? drain the in-flight turn, then exec-replace).
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger("stackowl.runtime")

# A frozen, comparable snapshot of the watched tree: sorted (path, mtime) pairs.
Signature = tuple[tuple[str, float], ...]


class CodeWatcher:
    """Polls ``*.py`` mtimes under ``watch_paths`` and fires ``on_change`` on settle."""

    def __init__(
        self,
        watch_paths: list[Path],
        on_change: Callable[[], None],
        *,
        poll_interval_s: float = 2.0,
        quiet_period_s: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._paths = [Path(p) for p in watch_paths]
        self._on_change = on_change
        self._poll_interval = poll_interval_s
        self._quiet_period = quiet_period_s
        self._clock = clock
        self._baseline: Signature | None = None
        # Debounce: the changed signature awaiting a quiet settle, and when it was
        # last observed to change. ``None`` = nothing pending.
        self._pending_sig: Signature | None = None
        self._pending_since: float = 0.0
        self._thread: threading.Thread | None = None
        self._running = False
        self._loop: asyncio.AbstractEventLoop | None = None

    def capture_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Record the asyncio loop to marshal ``on_change`` onto."""
        self._loop = loop

    def start(self) -> None:
        self._running = True
        self._baseline = self._scan()
        self._pending_sig = None
        if self._loop is None:
            try:
                self._loop = asyncio.get_running_loop()
            except RuntimeError:
                self._loop = None
        self._thread = threading.Thread(
            target=self._loop_body, daemon=True, name="code-watcher"
        )
        self._thread.start()
        log.info(
            "[runtime] CodeWatcher polling %s (every %.1fs, quiet %.0fs)",
            [str(p) for p in self._paths],
            self._poll_interval,
            self._quiet_period,
        )

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None

    def _scan(self) -> Signature:
        """Aggregate (path, mtime) over every ``*.py`` under the watched paths."""
        entries: list[tuple[str, float]] = []
        for root in self._paths:
            if root.is_file() and root.suffix == ".py":
                mtime = self._mtime(root)
                if mtime is not None:
                    entries.append((str(root), mtime))
                continue
            for path in root.rglob("*.py"):
                mtime = self._mtime(path)
                if mtime is not None:
                    entries.append((str(path), mtime))
        entries.sort()
        return tuple(entries)

    @staticmethod
    def _mtime(path: Path) -> float | None:
        try:
            return path.stat().st_mtime
        except OSError:
            return None

    def _loop_body(self) -> None:
        while self._running:
            time.sleep(self._poll_interval)
            self._check_once()

    def _check_once(self) -> None:
        """One poll tick: detect → quiet-period settle → fire once.

        A change vs the applied baseline is held *pending*; only after the
        signature is stable for ``quiet_period_s`` does ``on_change`` fire. A new
        change while pending resets the timer (debounce coalescing).
        """
        current = self._scan()
        if current == self._baseline:
            # Reverted to the applied state before settling — drop the pending mark.
            self._pending_sig = None
            return
        if current != self._pending_sig:
            # First sighting (or changed again) → (re)start the quiet timer.
            log.debug("[runtime] code change detected, awaiting %.0fs quiet", self._quiet_period)
            self._pending_sig = current
            self._pending_since = self._clock()
            return
        # Same change persisting — has it been quiet long enough?
        if self._clock() - self._pending_since < self._quiet_period:
            return
        self._baseline = current
        self._pending_sig = None
        log.info("[runtime] code change settled → requesting core restart")
        self._dispatch_change()

    def _dispatch_change(self) -> None:
        """Invoke ``on_change`` on the loop thread, never the watcher thread."""
        loop = self._loop
        if loop is None or not loop.is_running():
            self._on_change()
            return
        loop.call_soon_threadsafe(self._on_change)
