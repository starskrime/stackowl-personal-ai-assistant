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

import logging
import time
from collections.abc import Callable
from pathlib import Path

from stackowl.runtime.polled_daemon_thread import PolledDaemonThread

log = logging.getLogger("stackowl.runtime")

# A frozen, comparable snapshot of the watched tree: sorted (path, mtime) pairs.
Signature = tuple[tuple[str, float], ...]


class CodeWatcher(PolledDaemonThread):
    """Polls ``*.py`` mtimes under ``watch_paths`` and fires ``on_change`` on settle.

    Thread lifecycle (start/stop/loop capture/poll loop) lives in
    PolledDaemonThread, shared with ConfigWatcher — this class owns only the
    tree-scan + quiet-period debounce contract, which is genuinely different
    from ConfigWatcher's (a wall-clock quiet period here vs. settle-on-2-
    consecutive-polls there).
    """

    def __init__(
        self,
        watch_paths: list[Path],
        on_change: Callable[[], None],
        *,
        poll_interval_s: float = 2.0,
        quiet_period_s: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(poll_interval_s=poll_interval_s, thread_name="code-watcher")
        self._paths = [Path(p) for p in watch_paths]
        self._on_change = on_change
        self._quiet_period = quiet_period_s
        self._clock = clock
        self._baseline: Signature | None = None
        # Debounce: the changed signature awaiting a quiet settle, and when it was
        # last observed to change. ``None`` = nothing pending.
        self._pending_sig: Signature | None = None
        self._pending_since: float = 0.0

    def start(self) -> None:
        super().start()
        log.info(
            "[runtime] CodeWatcher polling %s (every %.1fs, quiet %.0fs)",
            [str(p) for p in self._paths],
            self._poll_interval,
            self._quiet_period,
        )

    def _on_start(self) -> None:
        self._baseline = self._scan()
        self._pending_sig = None

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
        # Invoke on_change on the loop thread, never the watcher thread —
        # PolledDaemonThread._dispatch owns that marshalling.
        self._dispatch(self._on_change)
