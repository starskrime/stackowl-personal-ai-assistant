"""The journal write-gate -- Spec 2.3's pause switch on ``journal.record()``.

A gateway/core Hello mismatch or a dropped link means the gateway cannot
prove its schema/registry match the core actually writing journal rows right
now, so new writes are refused (loud, typed) rather than silently accepted
against a possibly-stale schema. State is process-global, module-level --
mirrors ``journal/health.py``'s shape exactly: a ``threading.Lock``-guarded
flag, written from the gateway's connection-lifecycle code
(``GatewayLink.drop_connection`` / the Hello-mismatch branch in ``_route``),
read from the hot write path of every ``journal.record()`` call.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from stackowl.infra.observability import log


@dataclass
class WriteGateState:
    paused: bool = False
    reason: str | None = None

    def pause(self, reason: str) -> None:
        self.paused = True
        self.reason = reason

    def resume(self) -> None:
        self.paused = False
        self.reason = None


_state = WriteGateState()
_state_lock = threading.Lock()


def pause_writes(reason: str) -> None:
    """Refuse every ``journal.record()`` call until :func:`resume_writes`."""
    # 1. ENTRY / 2. STEP -- one flag write under the lock.
    log.journal.info(
        "[journal] write_gate.pause_writes: entry", extra={"_fields": {"reason": reason}},
    )
    with _state_lock:
        _state.pause(reason)
    # 4. EXIT
    log.journal.info(
        "[journal] write_gate.pause_writes: exit — journal writes REFUSED",
        extra={"_fields": {"reason": reason}},
    )


def resume_writes() -> None:
    """Let ``journal.record()`` writes through again (a confirmed-compatible Hello)."""
    # 1. ENTRY / 2. STEP -- one flag write under the lock.
    log.journal.info("[journal] write_gate.resume_writes: entry")
    with _state_lock:
        _state.resume()
    # 4. EXIT
    log.journal.info("[journal] write_gate.resume_writes: exit — journal writes RESUMED")


def writes_paused() -> bool:
    # 1. ENTRY -- DEBUG only: this runs on the hot path of every
    # journal.record() call (mirrors recorder.record's own DEBUG entry log).
    log.journal.debug("[journal] write_gate.writes_paused: entry")
    with _state_lock:
        paused = _state.paused
    # 4. EXIT
    log.journal.debug(
        "[journal] write_gate.writes_paused: exit", extra={"_fields": {"paused": paused}},
    )
    return paused


def reset_for_tests() -> None:
    """Clear the tracked state. Test-only -- the state is otherwise process-lifetime."""
    with _state_lock:
        _state.paused = False
        _state.reason = None
