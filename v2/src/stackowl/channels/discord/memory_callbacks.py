"""Memory fact approve/reject callback handlers for the Discord channel.

Mirrors :class:`~stackowl.channels.telegram.memory_callbacks.MemoryCallbackHandler`:
handles ``mem:approve:<fact_id>`` and ``mem:reject:<fact_id>`` ``custom_id``
values that originate from a memory-nudge inline keyboard. The action id carries
the FULL fact id (so the bridge's exact-match promote/delete actually moves the
fact — a truncated prefix would silently no-op).

Approved facts are force-promoted into committed memory (falling back to a
high-confidence stage when the bridge lacks ``force_promote``); rejected facts
are removed from the staged queue. The Discord interaction is ack'd by the
button-callback seam (``interaction.response.defer``), so this handler does not
ack out-of-band.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from stackowl.infra.observability import log
from stackowl.memory.trust import trust_for_source

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.channels.discord.callbacks import DiscordCallbackRouter
    from stackowl.memory.bridge import MemoryBridge

__all__ = ["DiscordMemoryCallbackHandler"]

_APPROVE_PREFIX = "mem:approve:"
_REJECT_PREFIX = "mem:reject:"


class DiscordMemoryCallbackHandler:
    """Wires Discord memory approve/reject taps to :class:`MemoryBridge` ops.

    Register with :meth:`register` to attach both prefix handlers to a
    :class:`DiscordCallbackRouter` in one call.
    """

    def __init__(self, memory_bridge: MemoryBridge) -> None:
        self._memory_bridge = memory_bridge
        log.discord.debug("[discord] memory_callbacks.handler.init: entry")

    async def handle_approve(self, callback_id: str, callback_data: str) -> None:
        """Handle a ``mem:approve:<fact_id>`` tap — promote the staged fact."""
        log.discord.debug(
            "[discord] memory_callbacks.handler.handle_approve: entry",
            extra={"_fields": {"data_prefix": callback_data[:24]}},
        )
        fact_id = callback_data.removeprefix(_APPROVE_PREFIX)
        try:
            if hasattr(self._memory_bridge, "force_promote"):
                await self._memory_bridge.force_promote(fact_id)
                log.discord.debug(
                    "[discord] memory_callbacks.handler.handle_approve: step force_promote",
                    extra={"_fields": {"fact_id": fact_id}},
                )
            else:
                from stackowl.memory.models import StagedFact

                fact = StagedFact(
                    fact_id=fact_id,
                    content="",
                    source_type="manual",
                    source_ref="discord:approval",
                    confidence=1.0,
                    trust=trust_for_source("manual"),
                )
                await self._memory_bridge.stage(fact)
                log.discord.debug(
                    "[discord] memory_callbacks.handler.handle_approve: step staged_at_1.0",
                    extra={"_fields": {"fact_id": fact_id}},
                )
        except Exception as exc:
            log.discord.error(
                "[discord] memory_callbacks.handler.handle_approve: bridge operation failed",
                exc_info=exc,
                extra={"_fields": {"fact_id": fact_id}},
            )
        log.discord.debug(
            "[discord] memory_callbacks.handler.handle_approve: exit",
            extra={"_fields": {"fact_id": fact_id}},
        )

    async def handle_reject(self, callback_id: str, callback_data: str) -> None:
        """Handle a ``mem:reject:<fact_id>`` tap — delete the staged fact."""
        log.discord.debug(
            "[discord] memory_callbacks.handler.handle_reject: entry",
            extra={"_fields": {"data_prefix": callback_data[:24]}},
        )
        fact_id = callback_data.removeprefix(_REJECT_PREFIX)
        try:
            await self._memory_bridge.delete(fact_id)
            log.discord.debug(
                "[discord] memory_callbacks.handler.handle_reject: step delete_called",
                extra={"_fields": {"fact_id": fact_id}},
            )
        except Exception as exc:
            log.discord.error(
                "[discord] memory_callbacks.handler.handle_reject: bridge delete failed",
                exc_info=exc,
                extra={"_fields": {"fact_id": fact_id}},
            )
        log.discord.debug(
            "[discord] memory_callbacks.handler.handle_reject: exit",
            extra={"_fields": {"fact_id": fact_id}},
        )

    def register(self, callback_router: DiscordCallbackRouter) -> None:
        """Attach both approve and reject handlers to ``callback_router``."""
        log.discord.debug("[discord] memory_callbacks.handler.register: entry")
        callback_router.register(_APPROVE_PREFIX, self.handle_approve)
        callback_router.register(_REJECT_PREFIX, self.handle_reject)
        log.discord.debug("[discord] memory_callbacks.handler.register: exit")
