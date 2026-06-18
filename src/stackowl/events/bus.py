"""EventBus — thin pub/sub used by config watcher and the proactivity bridge.

Sync handlers run INLINE in registration order (back-compat). A coroutine handler
is scheduled on the running asyncio loop via ``create_task`` so ``emit`` never
blocks the emitter (e.g. the scheduler poll thread) on a handler's network I/O
(C1/F105). Each scheduled task gets a done-callback that LOGS any exception — a
raising subscriber is isolated and never silently swallowed (B5). When no loop is
running a coroutine handler is run to completion via ``asyncio.run`` as a
fail-safe, still isolated.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Callable
from typing import Any

log = logging.getLogger("stackowl.events")

Handler = Callable[[Any], Any]


class EventBus:
    """Publish-subscribe bus supporting sync (inline) + async (scheduled) handlers."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = {}
        # Strong refs to in-flight handler tasks — without this a fire-and-forget
        # task can be GC'd mid-flight (loop holds only a weak ref), silently
        # dropping a proactive delivery. Discarded on completion.
        self._pending_tasks: set[asyncio.Task[Any]] = set()

    def subscribe(self, event: str, handler: Handler) -> None:
        self._handlers.setdefault(event, []).append(handler)

    def unsubscribe(self, event: str, handler: Handler) -> None:
        handlers = self._handlers.get(event, [])
        if handler in handlers:
            handlers.remove(handler)

    def emit(self, event: str, payload: Any = None) -> None:
        """Publish ``event`` to its subscribers.

        Threading contract (CFG-3 / F018): SYNC handlers run INLINE on the
        CALLER's thread. A handler that mutates asyncio-owned state (e.g. the
        provider registry / cost-tracker ``settings_reloaded`` consumers) is
        therefore only safe if ``emit`` is itself called on the owning loop
        thread, OR the handler is loop-agnostic. An off-loop emitter (the config
        watcher's daemon thread) MUST marshal to the loop —
        :meth:`ConfigWatcher._dispatch_reloaded` calls ``emit`` via
        ``loop.call_soon_threadsafe`` so its handlers run on the loop thread.
        ASYNC (coroutine) handlers are always scheduled on the running loop, so
        they are loop-safe regardless of the emitter's thread.
        """
        for handler in list(self._handlers.get(event, [])):
            if inspect.iscoroutinefunction(handler):
                self._dispatch_async(event, handler, payload)
                continue
            try:
                result = handler(payload)
            except Exception as exc:  # B5 — per-handler isolation, never silent
                log.error(
                    "event_bus.emit: handler raised for event %r",
                    event,
                    exc_info=exc,
                )
                continue
            # A sync handler that returned a coroutine (e.g. a lambda wrapping an
            # async call) is still scheduled rather than dropped.
            if inspect.iscoroutine(result):
                self._schedule(event, result)

    def _dispatch_async(self, event: str, handler: Handler, payload: Any) -> None:
        """Invoke a coroutine handler and schedule it without blocking emit()."""
        try:
            coro = handler(payload)
        except Exception as exc:  # B5 — the call itself raised before awaiting
            log.error(
                "event_bus.emit: async handler raised for event %r",
                event,
                exc_info=exc,
            )
            return
        if inspect.iscoroutine(coro):
            self._schedule(event, coro)

    def _schedule(self, event: str, coro: Any) -> None:
        """Schedule a coroutine on the running loop with a logging done-callback.

        Emit must never block the emitter on handler I/O, so the coroutine runs as
        a background task. With no running loop (sync context) it is run to
        completion as a fail-safe. Either way an exception is LOGGED, never lost.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop — run to completion as a fail-safe (isolated).
            try:
                asyncio.run(coro)
            except Exception as exc:  # B5 — never silent
                log.error(
                    "event_bus.emit: handler task failed (no loop) for event %r",
                    event,
                    exc_info=exc,
                )
            return
        task = loop.create_task(coro)
        # Retain a strong reference until completion so the task is not GC'd
        # mid-flight (the loop holds only a weak ref).
        self._pending_tasks.add(task)

        def _log_task_exc(t: asyncio.Task[Any]) -> None:
            self._pending_tasks.discard(t)
            if t.cancelled():
                return
            exc = t.exception()
            if exc is not None:  # B5 — isolate + log, never crash the loop
                log.error(
                    "event_bus.emit: handler task failed for event %r",
                    event,
                    exc_info=exc,
                )

        task.add_done_callback(_log_task_exc)
