"""NeedsYouMessageRegistry — gateway-side, in-memory, bounded map from a
durable needs_you item id to the Telegram message the platform sent for it
(Story 3.6).

WHY THIS EXISTS. `needs_you`'s own column list is closed by design (AD-28,
migration 0150's own comment) -- there is no `message_id` column, so the
gateway cannot ask the item itself "which message did I send for you". Every
surface that sends a Telegram message for an item (the approval keyboard, the
split-mode clarify text delivery, the new incident/alert pusher) registers
its `(chat_id, message_id)` here; the one generic cross-surface resolve hook
(`GatewayLink._deliver_journal_row`'s `needs_you.resolved` branch) pops it to
edit that exact message, regardless of which surface actually resolved the
item (a tap, a local timeout, the periodic expiry sweep, a future non-
Telegram surface).

GATEWAY-LOCAL AND EPHEMERAL, ON PURPOSE (Design Notes: "only the in-memory-
turn case is guaranteed durable in this epic"). A gateway restart loses any
unsent edit -- consistent with that boundary, not a regression: the item
itself is still durable and still resolves correctly, only the cosmetic
message-edit is best-effort.

BOUNDED, mirroring `TelegramConsentPrompter._decided`'s own LRU bound
(`_DECIDED_MEMORY`) -- an ever-growing dict is failure shape #4 in CLAUDE.md.
A forgotten entry simply means the LATER cross-surface edit is silently
skipped (the item still resolved correctly; only the cosmetic edit is lost),
never a crash or a wrong edit.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass

from stackowl.infra.observability import log

__all__ = [
    "NeedsYouMessageRegistry",
    "get_registry",
    "set_registry_for_tests",
]

#: Mirrors `TelegramConsentPrompter._DECIDED_MEMORY` -- sized for a busy
#: hour's worth of live items, never unbounded.
_MAX_ENTRIES = 256


@dataclass(slots=True, frozen=True)
class _Entry:
    chat_id: int
    message_id: int


class NeedsYouMessageRegistry:
    """Bounded LRU map: ``item_id -> (chat_id, message_id)``."""

    def __init__(self, *, max_entries: int = _MAX_ENTRIES) -> None:
        self._max_entries = max_entries
        self._entries: OrderedDict[str, _Entry] = OrderedDict()

    def remember(self, item_id: str, *, chat_id: int, message_id: int) -> None:
        """Record the Telegram message sent for ``item_id``. Never raises."""
        self._entries[item_id] = _Entry(chat_id=chat_id, message_id=message_id)
        self._entries.move_to_end(item_id)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)
        log.telegram.debug(
            "[telegram] needs_you_registry.remember",
            extra={"_fields": {
                "item_id": item_id, "chat_id": chat_id, "message_id": message_id,
            }},
        )

    def forget(self, item_id: str) -> tuple[int, int] | None:
        """Pop and return ``(chat_id, message_id)`` for ``item_id``, or
        ``None`` if nothing is registered (already popped, never registered,
        or aged out of the bound). Never raises."""
        entry = self._entries.pop(item_id, None)
        log.telegram.debug(
            "[telegram] needs_you_registry.forget",
            extra={"_fields": {"item_id": item_id, "found": entry is not None}},
        )
        if entry is None:
            return None
        return (entry.chat_id, entry.message_id)

    def peek(self, item_id: str) -> tuple[int, int] | None:
        """Read-only lookup — does not pop. Never raises."""
        entry = self._entries.get(item_id)
        return None if entry is None else (entry.chat_id, entry.message_id)


#: Module-level singleton — mirrors `TelegramConsentPrompter`'s own
#: per-process, in-memory state (`_pending`/`_decided`): every registrant and
#: the one cross-surface consumer share ONE process-wide registry, with no
#: explicit constructor-injection wiring needed at any of their call sites.
_registry = NeedsYouMessageRegistry()


def get_registry() -> NeedsYouMessageRegistry:
    """The one process-wide registry every registrant/consumer shares."""
    return _registry


def set_registry_for_tests(
    registry: NeedsYouMessageRegistry | None = None,
) -> NeedsYouMessageRegistry:
    """Test-only: replace the module-global registry with a fresh (or
    caller-supplied) one, mirroring the reset-seam convention used elsewhere
    in this package (e.g. `journal/health.py::reset_for_tests`)."""
    global _registry
    _registry = registry if registry is not None else NeedsYouMessageRegistry()
    return _registry
