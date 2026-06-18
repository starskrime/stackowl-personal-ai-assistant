"""Slack inbound-interactivity routing (Block Actions) + idempotency.

:class:`SlackActionRouter` mirrors the Telegram
:class:`~stackowl.channels.telegram.callbacks.CallbackRouter`: a prefix→handler
registry that dispatches a tapped button's ``action_id`` (or ``value``) to the
longest-matching registered handler. The Bolt ``@app.action`` registration that
FEEDS this router lives in the orchestrator (B3); this module builds the router
plus the per-prefix handlers.

Idempotency: Slack may re-deliver a ``block_actions`` payload (at-least-once on
network hiccups). Each delivery carries a unique identifier (Bolt's
``action_ts`` / the interaction trigger id); the router keeps a bounded
in-memory seen-set keyed by that ``delivery_id`` so a duplicate delivery does
NOT re-fire the handler. The set is in-memory (not SQLite-backed like Telegram's
store) because a Slack ack must complete within 3 seconds and the duplicate
window is short-lived; the bound prevents unbounded growth.

Fail-open: a handler that raises is contained (logged), and the delivery is
still marked processed so a retry is not re-fired — a half-applied action must
never be silently retried.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Awaitable, Callable

from stackowl.infra.observability import log

__all__ = ["SlackActionRouter"]

# A handler receives the raw action_id (or value) string the user tapped.
_Handler = Callable[[str], Awaitable[None]]

# Bound on the seen-set so a long-running process can't leak memory. Slack's
# duplicate-delivery window is seconds; this is generous headroom.
_MAX_SEEN = 4096


class SlackActionRouter:
    """Routes Slack ``block_actions`` taps to registered prefix-based handlers.

    Handlers are registered with a string prefix (``consent:``, ``clarify:``,
    ``memory_approve_``, ``memory_reject_``). On a tap the router finds the
    longest matching prefix, checks idempotency by ``delivery_id``, and calls the
    handler with the full action string.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, _Handler] = {}
        # Insertion-ordered so we can evict the oldest seen id when bounded.
        self._seen: OrderedDict[str, None] = OrderedDict()
        log.slack.debug("[slack] callbacks.router.init: entry")

    def register(self, prefix: str, handler: _Handler) -> None:
        """Register ``handler`` for action strings beginning with ``prefix``.

        Args:
            prefix: Exact prefix string to match at the start of the action id.
            handler: Async callable ``(action_id_or_value) -> None``.
        """
        log.slack.debug(
            "[slack] callbacks.router.register: entry",
            extra={"_fields": {"prefix": prefix}},
        )
        self._handlers[prefix] = handler
        log.slack.debug(
            "[slack] callbacks.router.register: exit",
            extra={"_fields": {"registered_count": len(self._handlers)}},
        )

    async def route(self, action_id_or_value: str, *, delivery_id: str) -> None:
        """Dispatch a Slack action tap to the longest-matching handler.

        4-point logging: entry / decision / step / exit.

        Idempotency: if ``delivery_id`` was already processed, the router returns
        without calling any handler (the duplicate is a no-op — the Bolt handler
        already ack'd the original). Each fresh ``delivery_id`` fires exactly
        once even across retries.

        Args:
            action_id_or_value: The tapped button's ``action_id`` (or ``value``),
                e.g. ``consent:abc:once`` / ``clarify:cid:2`` /
                ``memory_approve_deadbeef``.
            delivery_id: A unique-per-delivery identifier (Bolt ``action_ts`` /
                trigger id) used for at-least-once de-duplication.
        """
        log.slack.debug(
            "[slack] callbacks.router.route: entry",
            extra={
                "_fields": {
                    "action_prefix": action_id_or_value[:16],
                    "delivery_id_len": len(delivery_id),
                }
            },
        )

        if delivery_id in self._seen:
            log.slack.debug(
                "[slack] callbacks.router.route: duplicate delivery — skip",
                extra={"_fields": {"delivery_id_len": len(delivery_id)}},
            )
            return

        # Find matching handler by prefix (longest match wins — mirrors Telegram).
        handler: _Handler | None = None
        matched_prefix = ""
        for prefix, h in self._handlers.items():
            if action_id_or_value.startswith(prefix) and len(prefix) >= len(matched_prefix):
                handler = h
                matched_prefix = prefix

        log.slack.debug(
            "[slack] callbacks.router.route: decision handler_lookup",
            extra={"_fields": {"matched_prefix": matched_prefix or "none"}},
        )

        # Mark processed BEFORE invoking the handler so a handler that raises (and
        # may have applied a side effect) is not re-fired on a retry — fail-open
        # idempotency, mirrors the Telegram router's record-regardless contract.
        self._mark_seen(delivery_id)

        if handler is None:
            log.slack.warning(
                "[slack] callbacks.router.route: no handler for prefix",
                extra={"_fields": {"action_prefix": action_id_or_value[:16]}},
            )
            log.slack.debug("[slack] callbacks.router.route: exit — no handler")
            return

        try:
            await handler(action_id_or_value)
            log.slack.debug(
                "[slack] callbacks.router.route: step handler_done",
                extra={"_fields": {"matched_prefix": matched_prefix}},
            )
        except Exception as exc:  # fail-open — a tap must never crash the runner
            log.slack.error(
                "[slack] callbacks.router.route: handler raised",
                exc_info=exc,
                extra={"_fields": {"matched_prefix": matched_prefix}},
            )

        log.slack.debug(
            "[slack] callbacks.router.route: exit",
            extra={"_fields": {"matched_prefix": matched_prefix or "none"}},
        )

    def _mark_seen(self, delivery_id: str) -> None:
        """Record ``delivery_id`` as processed, evicting the oldest when bounded."""
        self._seen[delivery_id] = None
        while len(self._seen) > _MAX_SEEN:
            self._seen.popitem(last=False)
