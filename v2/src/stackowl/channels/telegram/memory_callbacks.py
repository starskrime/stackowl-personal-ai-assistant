"""Memory fact approve/reject callback handlers for the Telegram channel.

:class:`MemoryCallbackHandler` handles ``mem:approve:<fact_id>`` and
``mem:reject:<fact_id>`` callback_data values that originate from the inline
keyboards produced by :class:`~stackowl.channels.telegram.formatter.TelegramMemoryFormatter`
and :class:`~stackowl.channels.telegram.keyboard.InlineKeyboardBuilder`.

Approved facts are force-promoted into committed memory; rejected facts are
removed from the staged queue entirely.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from stackowl.infra.observability import log
from stackowl.tui.i18n import localize

if TYPE_CHECKING:
    from stackowl.channels.telegram.adapter import TelegramChannelAdapter
    from stackowl.channels.telegram.callbacks import CallbackRouter
    from stackowl.memory.bridge import MemoryBridge

__all__ = ["MemoryCallbackHandler"]

_APPROVE_PREFIX = "mem:approve:"
_REJECT_PREFIX = "mem:reject:"


class MemoryCallbackHandler:
    """Wires memory fact approval and rejection to :class:`MemoryBridge` operations.

    Register with :meth:`register` to attach both prefix handlers to a
    :class:`CallbackRouter` in one call.
    """

    def __init__(
        self,
        memory_bridge: "MemoryBridge",
        adapter: "TelegramChannelAdapter",
    ) -> None:
        self._memory_bridge = memory_bridge
        self._adapter = adapter
        log.telegram.debug("[telegram] memory_callbacks.handler.init: entry")

    async def handle_approve(self, callback_id: str, callback_data: str) -> None:
        """Handle a ``mem:approve:<fact_id>`` callback.

        Promotes the staged fact by calling :meth:`MemoryBridge.stage` with
        confidence 1.0 to trigger immediate promotion, then acknowledges the
        callback with a localised confirmation message.

        4-point logging: entry / decision / step / exit.

        Args:
            callback_id: Telegram callback query ID (for acknowledgement).
            callback_data: Full callback_data string, e.g. ``mem:approve:abc123``.
        """
        log.telegram.debug(
            "[telegram] memory_callbacks.handler.handle_approve: entry",
            extra={"_fields": {"callback_id_len": len(callback_id)}},
        )

        fact_id = callback_data.removeprefix(_APPROVE_PREFIX)
        log.telegram.debug(
            "[telegram] memory_callbacks.handler.handle_approve: decision parse_fact_id",
            extra={"_fields": {"fact_id": fact_id}},
        )

        try:
            # Attempt force_promote if available; fall back to high-confidence stage.
            if hasattr(self._memory_bridge, "force_promote"):
                await self._memory_bridge.force_promote(fact_id)  # type: ignore[attr-defined]
                log.telegram.debug(
                    "[telegram] memory_callbacks.handler.handle_approve: step force_promote",
                    extra={"_fields": {"fact_id": fact_id}},
                )
            else:
                from stackowl.memory.models import StagedFact

                fact = StagedFact(
                    fact_id=fact_id,
                    content="",
                    source_type="manual",
                    source_ref="telegram:approval",
                    confidence=1.0,
                )
                await self._memory_bridge.stage(fact)
                log.telegram.debug(
                    "[telegram] memory_callbacks.handler.handle_approve: step staged_at_1.0",
                    extra={"_fields": {"fact_id": fact_id}},
                )
        except Exception as exc:
            log.telegram.error(
                "[telegram] memory_callbacks.handler.handle_approve: bridge operation failed",
                exc,
                extra={"_fields": {"fact_id": fact_id}},
            )

        approved_text = localize("memory.approved")
        try:
            await self._adapter.acknowledge_callback(callback_id, text=approved_text)
        except Exception as exc:
            log.telegram.error(
                "[telegram] memory_callbacks.handler.handle_approve: acknowledge failed",
                exc,
                extra={"_fields": {"callback_id_len": len(callback_id)}},
            )

        log.telegram.debug(
            "[telegram] memory_callbacks.handler.handle_approve: exit",
            extra={"_fields": {"fact_id": fact_id}},
        )

    async def handle_reject(self, callback_id: str, callback_data: str) -> None:
        """Handle a ``mem:reject:<fact_id>`` callback.

        Deletes the staged fact from the memory bridge, then acknowledges the
        callback with a localised rejection message.

        4-point logging: entry / decision / step / exit.

        Args:
            callback_id: Telegram callback query ID (for acknowledgement).
            callback_data: Full callback_data string, e.g. ``mem:reject:abc123``.
        """
        log.telegram.debug(
            "[telegram] memory_callbacks.handler.handle_reject: entry",
            extra={"_fields": {"callback_id_len": len(callback_id)}},
        )

        fact_id = callback_data.removeprefix(_REJECT_PREFIX)
        log.telegram.debug(
            "[telegram] memory_callbacks.handler.handle_reject: decision parse_fact_id",
            extra={"_fields": {"fact_id": fact_id}},
        )

        try:
            await self._memory_bridge.delete(fact_id)
            log.telegram.debug(
                "[telegram] memory_callbacks.handler.handle_reject: step delete_called",
                extra={"_fields": {"fact_id": fact_id}},
            )
        except Exception as exc:
            log.telegram.error(
                "[telegram] memory_callbacks.handler.handle_reject: bridge delete failed",
                exc,
                extra={"_fields": {"fact_id": fact_id}},
            )

        rejected_text = localize("memory.rejected")
        try:
            await self._adapter.acknowledge_callback(callback_id, text=rejected_text)
        except Exception as exc:
            log.telegram.error(
                "[telegram] memory_callbacks.handler.handle_reject: acknowledge failed",
                exc,
                extra={"_fields": {"callback_id_len": len(callback_id)}},
            )

        log.telegram.debug(
            "[telegram] memory_callbacks.handler.handle_reject: exit",
            extra={"_fields": {"fact_id": fact_id}},
        )

    def register(self, callback_router: "CallbackRouter") -> None:
        """Attach both approve and reject handlers to ``callback_router``.

        Args:
            callback_router: The :class:`CallbackRouter` instance to register with.
        """
        log.telegram.debug("[telegram] memory_callbacks.handler.register: entry")
        callback_router.register(_APPROVE_PREFIX, self.handle_approve)
        callback_router.register(_REJECT_PREFIX, self.handle_reject)
        log.telegram.debug("[telegram] memory_callbacks.handler.register: exit")
