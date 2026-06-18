"""Telegram inline-button callback routing and idempotency.

:class:`CallbackIdempotencyStore` persists processed ``callback_id`` values in
a SQLite table so that duplicate deliveries (Telegram may re-deliver callbacks
on network failures) are handled gracefully.

:class:`CallbackRouter` dispatches incoming ``callback_query`` events to
registered handlers keyed by ``callback_data`` prefix, and enforces
idempotency via :class:`CallbackIdempotencyStore`.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from stackowl.db.pool import DbPool
from stackowl.infra.observability import log

if TYPE_CHECKING:
    from stackowl.channels.telegram.adapter import TelegramChannelAdapter

__all__ = [
    "CallbackIdempotencyStore",
    "CallbackRouter",
]

_Handler = Callable[[str, str], Awaitable[None]]


class CallbackIdempotencyStore:
    """SQLite-backed store that tracks processed Telegram callback IDs.

    Telegram guarantees at-least-once delivery for callback_query events;
    this store provides exactly-once processing semantics.
    """

    def __init__(self, db_pool: DbPool) -> None:
        self._pool = db_pool
        log.telegram.debug("[telegram] callbacks.idempotency.init: entry")

    async def ensure_table(self) -> None:
        """Create the ``callback_log`` table if it does not already exist.

        Safe to call repeatedly — uses ``CREATE TABLE IF NOT EXISTS``.
        """
        log.telegram.debug("[telegram] callbacks.idempotency.ensure_table: entry")
        await self._pool.execute(
            """
            CREATE TABLE IF NOT EXISTS callback_log (
                callback_id TEXT PRIMARY KEY,
                callback_data TEXT NOT NULL,
                processed_at REAL NOT NULL
            )
            """
        )
        log.telegram.debug("[telegram] callbacks.idempotency.ensure_table: exit")

    async def is_processed(self, callback_id: str) -> bool:
        """Return ``True`` if ``callback_id`` has already been processed.

        4-point logging: entry / decision / step / exit.
        """
        log.telegram.debug(
            "[telegram] callbacks.idempotency.is_processed: entry",
            extra={"_fields": {"callback_id_len": len(callback_id)}},
        )
        rows = await self._pool.fetch_all(
            "SELECT 1 FROM callback_log WHERE callback_id = ?",
            (callback_id,),
        )
        found = len(rows) > 0
        log.telegram.debug(
            "[telegram] callbacks.idempotency.is_processed: decision result",
            extra={"_fields": {"found": found}},
        )
        log.telegram.debug(
            "[telegram] callbacks.idempotency.is_processed: exit",
            extra={"_fields": {"found": found}},
        )
        return found

    async def mark_processed(self, callback_id: str, callback_data: str) -> None:
        """Record ``callback_id`` as processed (INSERT OR IGNORE — idempotent).

        4-point logging: entry / decision / step / exit.
        """
        log.telegram.debug(
            "[telegram] callbacks.idempotency.mark_processed: entry",
            extra={"_fields": {"callback_id_len": len(callback_id)}},
        )
        now = time.time()
        log.telegram.debug(
            "[telegram] callbacks.idempotency.mark_processed: decision insert_or_ignore",
            extra={"_fields": {}},
        )
        await self._pool.execute(
            "INSERT OR IGNORE INTO callback_log (callback_id, callback_data, processed_at) "
            "VALUES (?, ?, ?)",
            (callback_id, callback_data, now),
        )
        log.telegram.debug(
            "[telegram] callbacks.idempotency.mark_processed: exit",
            extra={"_fields": {"callback_id_len": len(callback_id)}},
        )


class CallbackRouter:
    """Routes Telegram callback_query events to registered prefix-based handlers.

    Handlers are registered with a string prefix.  When a callback_query
    arrives, the router finds the longest matching prefix, checks idempotency,
    calls the handler, records the callback, and acknowledges it via the
    adapter.
    """

    def __init__(
        self,
        db_pool: DbPool,
        adapter: "TelegramChannelAdapter",
    ) -> None:
        self._pool = db_pool
        self._adapter = adapter
        self._store = CallbackIdempotencyStore(db_pool)
        self._handlers: dict[str, _Handler] = {}
        log.telegram.debug("[telegram] callbacks.router.init: entry")

    async def ensure_table(self) -> None:
        """Ensure the idempotency table exists (delegates to the store)."""
        await self._store.ensure_table()

    def register(self, prefix: str, handler: _Handler) -> None:
        """Register ``handler`` for ``callback_data`` values beginning with ``prefix``.

        Args:
            prefix: Exact prefix string to match at the start of callback_data.
            handler: Async callable ``(callback_id, callback_data) -> None``.
        """
        log.telegram.debug(
            "[telegram] callbacks.router.register: entry",
            extra={"_fields": {"prefix": prefix}},
        )
        self._handlers[prefix] = handler
        log.telegram.debug(
            "[telegram] callbacks.router.register: exit",
            extra={"_fields": {"registered_count": len(self._handlers)}},
        )

    async def route(self, update: Any, context: Any) -> None:
        """PTB callback that dispatches callback_query events.

        4-point logging: entry / decision / step / exit.

        Idempotency: if the callback_id has already been processed, the router
        acknowledges the duplicate without calling any handler.

        Args:
            update: python-telegram-bot ``Update`` object.
            context: python-telegram-bot ``ContextTypes.DEFAULT_TYPE``.
        """
        log.telegram.debug("[telegram] callbacks.router.route: entry")

        cq = update.callback_query
        if cq is None:
            log.telegram.debug(
                "[telegram] callbacks.router.route: no callback_query — skip"
            )
            return

        callback_id: str = cq.id
        callback_data: str = cq.data or ""

        log.telegram.debug(
            "[telegram] callbacks.router.route: decision check_idempotency",
            extra={"_fields": {"callback_id_len": len(callback_id)}},
        )

        try:
            already_done = await self._store.is_processed(callback_id)
        except Exception as exc:
            log.telegram.error(
                "[telegram] callbacks.router.route: idempotency check failed",
                exc,
                extra={"_fields": {}},
            )
            already_done = False

        if already_done:
            log.telegram.debug(
                "[telegram] callbacks.router.route: duplicate — acknowledge and skip",
                extra={"_fields": {"callback_id_len": len(callback_id)}},
            )
            try:
                await self._adapter.acknowledge_callback(callback_id)
            except Exception as exc:
                log.telegram.error(
                    "[telegram] callbacks.router.route: acknowledge duplicate failed",
                    exc,
                    extra={"_fields": {}},
                )
            return

        # Find matching handler by prefix (longest match wins).
        handler: _Handler | None = None
        matched_prefix = ""
        for prefix, h in self._handlers.items():
            if callback_data.startswith(prefix) and len(prefix) >= len(matched_prefix):
                handler = h
                matched_prefix = prefix

        log.telegram.debug(
            "[telegram] callbacks.router.route: step handler_lookup",
            extra={"_fields": {"matched_prefix": matched_prefix or "none"}},
        )

        if handler is None:
            log.telegram.warning(
                "[telegram] callbacks.router.route: no handler for prefix",
                extra={"_fields": {"data_prefix_8": callback_data[:8]}},
            )
        else:
            try:
                await handler(callback_id, callback_data)
            except Exception as exc:
                log.telegram.error(
                    "[telegram] callbacks.router.route: handler raised",
                    exc,
                    extra={"_fields": {"matched_prefix": matched_prefix}},
                )

        # Record and acknowledge regardless of handler outcome (fail-open
        # acknowledgement prevents Telegram from showing a spinner indefinitely).
        try:
            await self._store.mark_processed(callback_id, callback_data)
        except Exception as exc:
            log.telegram.error(
                "[telegram] callbacks.router.route: mark_processed failed",
                exc,
                extra={"_fields": {}},
            )

        try:
            await self._adapter.acknowledge_callback(callback_id)
        except Exception as exc:
            log.telegram.error(
                "[telegram] callbacks.router.route: acknowledge_callback failed",
                exc,
                extra={"_fields": {}},
            )

        log.telegram.debug(
            "[telegram] callbacks.router.route: exit",
            extra={"_fields": {"callback_id_len": len(callback_id)}},
        )
