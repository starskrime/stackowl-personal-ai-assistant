"""CFG-3 (F018) — settings_reloaded is marshalled to the captured asyncio loop.

The watcher runs on a daemon thread; mutating asyncio-owned state from there is
unsafe. When a running loop was captured at start(), the watcher dispatches the
reload via ``loop.call_soon_threadsafe`` so subscribers run ON the loop thread,
not the watcher thread. With no loop captured it falls back to a direct call (a
loop-agnostic subscriber is unaffected) — the documented contract.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import pytest

from stackowl.config.watcher import ConfigWatcher
from stackowl.events.bus import EventBus


@pytest.mark.asyncio
async def test_reload_runs_on_loop_thread_when_loop_captured(tmp_path: Path) -> None:
    loop = asyncio.get_running_loop()
    bus = EventBus()
    seen_threads: list[int] = []

    def _handler(_payload: object) -> None:
        seen_threads.append(threading.get_ident())

    bus.subscribe("settings_reloaded", _handler)
    w = ConfigWatcher(tmp_path / "c.yaml", bus, lambda: object(), poll_interval=5.0)
    w.capture_loop(loop)

    # Fire the reload from a DIFFERENT (watcher-like) thread; the handler must
    # execute on the captured loop thread, not the calling thread.
    caller_thread = {"id": 0}

    def _fire() -> None:
        caller_thread["id"] = threading.get_ident()
        w._reload()

    t = threading.Thread(target=_fire)
    t.start()
    t.join()

    # Let the loop process the scheduled callback.
    await asyncio.sleep(0.05)

    assert seen_threads, "handler never ran"
    assert seen_threads[0] == threading.get_ident(), "handler did not run on loop thread"
    assert seen_threads[0] != caller_thread["id"], "handler ran on the caller thread"


def test_reload_direct_when_no_loop_captured(tmp_path: Path) -> None:
    # No loop captured → direct emit (loop-agnostic subscriber). Back-compat.
    bus = EventBus()
    got: list[object] = []
    bus.subscribe("settings_reloaded", got.append)
    w = ConfigWatcher(tmp_path / "c.yaml", bus, lambda: "S", poll_interval=5.0)
    w._reload()
    assert got == ["S"]
