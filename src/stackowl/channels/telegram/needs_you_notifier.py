"""TelegramIncidentAlertNotifier — gateway-side pusher for `incident`/`alert`
needs_you items that have no live waiter (Story 3.6).

WHY THIS EXISTS. `approval`/`question` items already deliver to Telegram
through their own live prompters (`TelegramConsentPrompter`, the clarify
gateway's delivery path) because a turn is BLOCKED waiting for them.
`incident`/`alert` items (`consent.channel_unreachable`, `budget.warning`,
`heal.exhausted`, `job.parked`) have no such waiter — nothing in the codebase
ever pushed them to Telegram before this story, so the owner never saw one
unless they happened to be watching the TUI at the right moment.

Reacts to `needs_you.opened` for those two kinds ONLY (`GatewayLink.
_deliver_journal_row` filters before calling this) — approval/question are
explicitly excluded there so this notifier can never double-deliver an item
already handled by its own live prompter.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from stackowl.channels.telegram.needs_you_registry import get_registry
from stackowl.infra.observability import log

if TYPE_CHECKING:  # pragma: no cover -- typing-only
    from stackowl.journal.enums import NeedsYouKind

__all__ = ["TelegramIncidentAlertNotifier"]


class _SupportsSendText(Protocol):
    async def send_text(
        self, text: str, *, chat_id: int | None = None
    ) -> Any: ...


class TelegramIncidentAlertNotifier:
    """Sends the narrator's `full` text for a newly-opened incident/alert
    item as plain text, and registers the sent message so a later
    `needs_you.resolved` can edit it (the shared cross-surface hook in
    `GatewayLink._deliver_journal_row`)."""

    def __init__(self, adapter: _SupportsSendText) -> None:
        self._adapter = adapter

    async def deliver_opened(
        self, item_id: str, kind: NeedsYouKind, chat_id: int, text: str,
    ) -> None:
        """Best-effort: send `text` to `chat_id`, register the result.

        Never raises. Adapter/send failure: logged, swallowed, the item
        stays open until its own expiry (spec I/O matrix) — this is a
        notification, not the item's own durability.
        """
        # 1. ENTRY
        log.telegram.debug(
            "[telegram] needs_you_notifier.deliver_opened: entry",
            extra={"_fields": {"item_id": item_id, "kind": kind.value, "chat_id": chat_id}},
        )
        # 2/3. DECISION+STEP — plain text, no keyboard: incident/alert items
        # have no action to take from the message itself (spec Boundaries).
        try:
            message = await self._adapter.send_text(text, chat_id=chat_id)
        except Exception as exc:  # noqa: BLE001 — best-effort, never raises
            log.telegram.error(
                "[telegram] needs_you_notifier.deliver_opened: send failed — "
                "item stays open until its own expiry",
                exc_info=exc,
                extra={"_fields": {"item_id": item_id, "kind": kind.value}},
            )
            return
        message_id = getattr(message, "message_id", None)
        if message_id is None:
            log.telegram.debug(
                "[telegram] needs_you_notifier.deliver_opened: no message_id "
                "returned — nothing to register",
                extra={"_fields": {"item_id": item_id}},
            )
            return
        get_registry().remember(item_id, chat_id=chat_id, message_id=message_id)
        # 4. EXIT
        log.telegram.info(
            "[telegram] needs_you_notifier.deliver_opened: exit — delivered",
            extra={"_fields": {"item_id": item_id, "kind": kind.value, "chat_id": chat_id}},
        )
