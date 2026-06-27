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
from collections.abc import Awaitable, Callable
from typing import Protocol

from stackowl.infra.observability import log

# User-facing notice sink (async). Receives an already-composed message.
NotifySink = Callable[[str], Awaitable[None]]
# Returns True iff a durable checkpoint exists for the abandoned straggler(s),
# i.e. the turn will actually resume in the new core. Sync (a cheap store probe).
CheckpointProbe = Callable[[], bool]

# F-37 — what the user sees when their turn is cut short by a restart. Two
# variants: one when the turn is genuinely durable (will resume), one when it is
# NOT (must be retried). We never assert resume unconditionally.
_NOTICE_RESUMABLE = (
    "Your request was interrupted by a restart — it's durable and will continue "
    "automatically."
)
_NOTICE_NOT_RESUMABLE = (
    "Your request was interrupted by a restart and could not be resumed — please "
    "send it again. Retrying now."
)
_NOTICE_UNKNOWN = (
    "Your request was interrupted by a restart — retrying. If you don't get a "
    "reply shortly, please resend it."
)


class _Drainable(Protocol):
    def has_active_turns(self) -> bool: ...  # noqa: D102

    def active_turn_count(self) -> int: ...  # noqa: D102


async def quiesce(
    turn_registry: _Drainable,
    *,
    grace_seconds: float,
    poll_interval_s: float = 0.5,
    notify: NotifySink | None = None,
    has_checkpoint: CheckpointProbe | None = None,
) -> bool:
    """Wait for all RUNNING turns to finish, up to ``grace_seconds``.

    Returns True if the core drained cleanly (no active turns), False if the
    grace ceiling elapsed with stragglers still running. Never raises — a
    restart proceeds regardless; the bool just tells the caller whether any
    turn was abandoned (for an operator-visible log).

    F-37 — when a straggler IS abandoned, ``notify`` (if supplied) emits a
    user-facing "interrupted by restart, retrying" message so the cut-off turn is
    not silently dropped, and ``has_checkpoint`` decides WHICH message: durable
    resume is only PROMISED when a checkpoint actually exists for that straggler
    (we never assert automatic resume unconditionally). Both are best-effort: a
    failing ``notify`` or ``has_checkpoint`` is logged and the restart proceeds.
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
            abandoned = turn_registry.active_turn_count()
            resumable = _probe_checkpoint(has_checkpoint)
            log.gateway.warning(
                "[runtime] quiesce: grace ceiling reached — restarting with "
                "stragglers still running",
                extra={"_fields": {"abandoned": abandoned, "resumable": resumable}},
            )
            await _notify_straggler(notify, resumable)
            return False
        await asyncio.sleep(poll_interval_s)

    log.gateway.info("[runtime] quiesce: turns drained — safe to restart")
    return True


def _probe_checkpoint(has_checkpoint: CheckpointProbe | None) -> bool | None:
    """Evaluate the checkpoint probe. ``None`` = unknown (no probe / probe error)
    so we never falsely PROMISE durable resume."""
    if has_checkpoint is None:
        return None
    try:
        return bool(has_checkpoint())
    except Exception as exc:  # noqa: BLE001 — advisory probe, never block restart
        log.gateway.error(
            "[runtime] quiesce: checkpoint probe raised — treating as unknown",
            exc_info=exc,
        )
        return None


async def _notify_straggler(notify: NotifySink | None, resumable: bool | None) -> None:
    """Emit the user-facing interrupted notice (best-effort)."""
    if notify is None:
        return
    if resumable is True:
        message = _NOTICE_RESUMABLE
    elif resumable is False:
        message = _NOTICE_NOT_RESUMABLE
    else:
        message = _NOTICE_UNKNOWN
    try:
        await notify(message)
    except Exception as exc:  # noqa: BLE001 — a broken sink must not block restart
        log.gateway.error(
            "[runtime] quiesce: straggler notify failed", exc_info=exc
        )
