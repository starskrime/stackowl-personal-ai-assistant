"""Memory fact approve/reject action handlers for the Slack channel.

:class:`SlackMemoryActionHandler` handles ``memory_approve_<short>`` and
``memory_reject_<short>`` action ids that originate from the Block Kit nudge
produced by
:meth:`~stackowl.channels.slack.helpers.SlackBlockKitFormatter.format_memory_nudge`.
Those action ids carry only the FIRST 8 chars of the fact id (the formatter
keeps the full id out of the payload), so the bridge operations are keyed on
that short prefix — matching the Telegram pattern's intent while honoring the
Slack formatter's id-truncation.

Approved facts are force-promoted into committed memory (falling back to a
high-confidence stage when the bridge lacks ``force_promote``); rejected facts
are removed from the staged queue. Slack ``block_actions`` are ack'd by the Bolt
handler (B3), so this handler does not ack out-of-band.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from stackowl.infra.observability import log
from stackowl.memory.trust import trust_for_source

if TYPE_CHECKING:
    from stackowl.channels.slack.callbacks import SlackActionRouter
    from stackowl.memory.bridge import MemoryBridge

__all__ = ["SlackMemoryActionHandler"]

_APPROVE_PREFIX = "memory_approve_"
_REJECT_PREFIX = "memory_reject_"


class SlackMemoryActionHandler:
    """Wires Slack memory approve/reject taps to :class:`MemoryBridge` operations.

    Register with :meth:`register` to attach both prefix handlers to a
    :class:`SlackActionRouter` in one call.
    """

    def __init__(self, memory_bridge: MemoryBridge) -> None:
        self._memory_bridge = memory_bridge
        log.slack.debug("[slack] memory_callbacks.handler.init: entry")

    async def handle_approve(self, action_id_or_value: str) -> None:
        """Handle a ``memory_approve_<short>`` tap — promote the staged fact.

        4-point logging: entry / decision / step / exit.
        """
        log.slack.debug(
            "[slack] memory_callbacks.handler.handle_approve: entry",
            extra={"_fields": {"action_prefix": action_id_or_value[:24]}},
        )
        fact_id = action_id_or_value.removeprefix(_APPROVE_PREFIX)
        log.slack.debug(
            "[slack] memory_callbacks.handler.handle_approve: decision parse_fact_id",
            extra={"_fields": {"fact_id_short": fact_id}},
        )
        try:
            if hasattr(self._memory_bridge, "force_promote"):
                await self._memory_bridge.force_promote(fact_id)
                log.slack.debug(
                    "[slack] memory_callbacks.handler.handle_approve: step force_promote",
                    extra={"_fields": {"fact_id_short": fact_id}},
                )
            else:
                from stackowl.memory.models import StagedFact

                fact = StagedFact(
                    fact_id=fact_id,
                    content="",
                    source_type="manual",
                    source_ref="slack:approval",
                    confidence=1.0,
                    trust=trust_for_source("manual"),
                )
                await self._memory_bridge.stage(fact)
                log.slack.debug(
                    "[slack] memory_callbacks.handler.handle_approve: step staged_at_1.0",
                    extra={"_fields": {"fact_id_short": fact_id}},
                )
        except Exception as exc:
            log.slack.error(
                "[slack] memory_callbacks.handler.handle_approve: bridge operation failed",
                exc_info=exc,
                extra={"_fields": {"fact_id_short": fact_id}},
            )
        log.slack.debug(
            "[slack] memory_callbacks.handler.handle_approve: exit",
            extra={"_fields": {"fact_id_short": fact_id}},
        )

    async def handle_reject(self, action_id_or_value: str) -> None:
        """Handle a ``memory_reject_<short>`` tap — delete the staged fact.

        4-point logging: entry / decision / step / exit.
        """
        log.slack.debug(
            "[slack] memory_callbacks.handler.handle_reject: entry",
            extra={"_fields": {"action_prefix": action_id_or_value[:24]}},
        )
        fact_id = action_id_or_value.removeprefix(_REJECT_PREFIX)
        log.slack.debug(
            "[slack] memory_callbacks.handler.handle_reject: decision parse_fact_id",
            extra={"_fields": {"fact_id_short": fact_id}},
        )
        try:
            await self._memory_bridge.delete(fact_id)
            log.slack.debug(
                "[slack] memory_callbacks.handler.handle_reject: step delete_called",
                extra={"_fields": {"fact_id_short": fact_id}},
            )
        except Exception as exc:
            log.slack.error(
                "[slack] memory_callbacks.handler.handle_reject: bridge delete failed",
                exc_info=exc,
                extra={"_fields": {"fact_id_short": fact_id}},
            )
        log.slack.debug(
            "[slack] memory_callbacks.handler.handle_reject: exit",
            extra={"_fields": {"fact_id_short": fact_id}},
        )

    def register(self, router: SlackActionRouter) -> None:
        """Attach both approve and reject handlers to ``router``."""
        log.slack.debug("[slack] memory_callbacks.handler.register: entry")
        router.register(_APPROVE_PREFIX, self.handle_approve)
        router.register(_REJECT_PREFIX, self.handle_reject)
        log.slack.debug("[slack] memory_callbacks.handler.register: exit")
