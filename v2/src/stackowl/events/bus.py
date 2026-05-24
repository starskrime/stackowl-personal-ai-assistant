"""Synchronous EventBus — thin pub/sub used by config watcher and future subsystems."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

log = logging.getLogger("stackowl.events")

Handler = Callable[[Any], None]


class EventBus:
    """Simple synchronous publish-subscribe bus.

    Subscribers receive events in registration order.  Async support is added
    in a later story when the asyncio loop is running.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = {}

    def subscribe(self, event: str, handler: Handler) -> None:
        self._handlers.setdefault(event, []).append(handler)

    def unsubscribe(self, event: str, handler: Handler) -> None:
        handlers = self._handlers.get(event, [])
        if handler in handlers:
            handlers.remove(handler)

    def emit(self, event: str, payload: Any = None) -> None:
        for handler in list(self._handlers.get(event, [])):
            try:
                handler(payload)
            except Exception as exc:
                log.error(
                    "event_bus.emit: handler raised for event %r",
                    event,
                    exc_info=exc,
                )
