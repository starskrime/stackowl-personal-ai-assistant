"""Graceful quiesce for the restartable core — drain in-flight turns before exec.

On a code-change (or manual) restart the core must not yank a running turn out
from under the user. ``quiesce`` polls the shared :class:`TurnRegistry` until no
turn is RUNNING (``has_active_turns()`` is False) or a ``grace_seconds`` ceiling
elapses. The caller stops *accepting* new turns first (so the running set can
only shrink), waits here, then runs teardown and ``os.execv``.

Stragglers past the ceiling are the caller's call: durable turns are already
checkpointed (they resume in the new core under the same request_id), so the
honest, dev-friendly default is to log what is being abandoned and restart
anyway — a hot-reload that waits forever would defeat its own purpose.
"""

from __future__ import annotations

import asyncio
from typing import Protocol

from stackowl.infra.observability import log


class _Drainable(Protocol):
    def has_active_turns(self) -> bool: ...  # noqa: D102

    def active_turn_count(self) -> int: ...  # noqa: D102


async def quiesce(
    turn_registry: _Drainable,
    *,
    grace_seconds: float,
    poll_interval_s: float = 0.5,
) -> bool:
    """Wait for all RUNNING turns to finish, up to ``grace_seconds``.

    Returns True if the core drained cleanly (no active turns), False if the
    grace ceiling elapsed with stragglers still running. Never raises — a
    restart proceeds regardless; the bool just tells the caller whether any
    turn was abandoned (for an operator-visible log).
    """
    if not turn_registry.has_active_turns():
        log.gateway.info("[runtime] quiesce: no active turns — draining clean")
        return True

    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.0, grace_seconds)
    log.gateway.info(
        "[runtime] quiesce: waiting for turns to drain",
        extra={"_fields": {
            "active": turn_registry.active_turn_count(),
            "grace_seconds": grace_seconds,
        }},
    )
    while turn_registry.has_active_turns():
        if loop.time() >= deadline:
            log.gateway.warning(
                "[runtime] quiesce: grace ceiling reached — restarting with "
                "stragglers still running",
                extra={"_fields": {"abandoned": turn_registry.active_turn_count()}},
            )
            return False
        await asyncio.sleep(poll_interval_s)

    log.gateway.info("[runtime] quiesce: turns drained — safe to restart")
    return True
