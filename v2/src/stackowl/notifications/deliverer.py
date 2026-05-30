"""ProactiveDeliverer — the outbound transport bridge (E7-S0).

The :class:`NotificationRouter` is a pure decision/audit component: it decides
``delivered`` / ``batched`` / ``suppressed`` and writes the audit row, but never
touches a channel adapter. This module is the missing bridge — it asks the
router for a decision and, only on ``delivered``, resolves the target channel
adapter and transports the message verbatim via ``send_text``.

Design (kept thin — this is StackOwl glue, not a vendor port):

* ``batched`` / ``suppressed`` decisions are already handled by the router
  (queued / logged) — the deliverer returns them untouched, no send.
* Self-healing ([[feedback_always_self_healing]]): an unknown channel or a
  failing ``send_text`` is caught, logged at ``error`` (B5), and surfaced as a
  terminal ``"failed"`` status — :meth:`deliver` NEVER raises into its caller.
  A single bounded retry covers a transient send error before failing.
* The deliverer emits NO new user-facing text — it transports the router-vetted
  message body verbatim.
"""

from __future__ import annotations

import time as _time
from typing import TYPE_CHECKING, Literal

from stackowl.infra.observability import log
from stackowl.notifications.router import DeliveryStatus

if TYPE_CHECKING:  # pragma: no cover — typing-only imports
    from stackowl.channels.registry import ChannelRegistry
    from stackowl.config.settings import Settings
    from stackowl.notifications.router import Notification, NotificationRouter


# Urgency an agent-originated notification is permitted to request. ``critical``
# is reserved for user / job-config / system origin and is clamped down to
# ``normal`` for agent callers (S2 heartbeat_respond, S3 send_message).
AgentUrgency = Literal["normal", "low"]


def clamp_agent_urgency(requested: str) -> AgentUrgency:
    """Clamp an agent-requested urgency to the agent-permitted set.

    ``normal`` / ``low`` pass through unchanged; anything else (notably
    ``critical``) is clamped to ``normal``. Pure function — no clock, no I/O.
    System callers (e.g. ``/urgent``) do NOT use this clamp and keep their
    ability to send ``critical``.
    """
    if requested == "low":
        return "low"
    return "normal"


class ProactiveDeliverer:
    """Transports a router-vetted notification to its channel adapter.

    Holds the (decision) router and the (transport) channel registry. The
    registry singleton is resolved once at construction (in assembly) and
    injected — :meth:`deliver` never reaches for the singleton itself.
    """

    def __init__(
        self,
        router: NotificationRouter,
        registry: ChannelRegistry,
        settings: Settings,
    ) -> None:
        self._router = router
        self._registry = registry
        self._settings = settings

    async def deliver(self, notification: Notification) -> DeliveryStatus:
        """Route + transport ``notification``; never raises.

        Returns the router decision verbatim for ``batched`` / ``suppressed``
        (the router already queued / logged those), the router's ``delivered``
        on a successful ``send_text``, or ``"failed"`` if transport could not
        complete (unknown channel / adapter error after one retry).
        """
        # 1. ENTRY
        log.notifications.debug(
            "[notifications] deliverer.deliver: entry",
            extra={
                "_fields": {
                    "urgency": notification.urgency,
                    "category": notification.category,
                    "channel": notification.channel_name,
                }
            },
        )
        t0 = _time.monotonic()

        status = await self._router.deliver(notification)
        channel = notification.channel_name or self._settings.notifications.default_channel

        # 2. DECISION — only a ``delivered`` decision triggers transport.
        if status != "delivered":
            log.notifications.debug(
                "[notifications] deliverer.deliver: no transport (router-handled)",
                extra={"_fields": {"status": status, "channel": channel}},
            )
            self._log_exit(status, channel, t0)
            return status

        # 3. STEP — resolve adapter + transport the body (bounded retry-once).
        result = await self._transport(channel, notification.message)
        self._log_exit(result, channel, t0)
        return result

    async def transport(self, channel: str, message: str) -> DeliveryStatus:
        """Transport an already-decided message body to ``channel``.

        Used by the digest flush, where the routing decision was made when the
        notification was first batched — re-deciding here would be wrong. Same
        self-healing contract as :meth:`deliver`: never raises; ``"failed"`` on
        unknown channel or a send that fails after one retry.
        """
        log.notifications.debug(
            "[notifications] deliverer.transport: entry",
            extra={"_fields": {"channel": channel}},
        )
        return await self._transport(channel, message)

    async def _transport(self, channel: str, message: str) -> DeliveryStatus:
        """Resolve the adapter and send ``message``; retry-once on send error.

        Returns ``"delivered"`` on success or ``"failed"`` (logged) on an
        unknown channel or a send that still fails after one retry. Never raises.
        """
        try:
            adapter = self._registry.get(channel)
        except Exception as exc:  # B5 — unknown / unavailable channel
            log.notifications.error(
                "[notifications] deliverer._transport: channel unavailable",
                exc_info=exc,
                extra={"_fields": {"channel": channel}},
            )
            return "failed"

        for attempt in (1, 2):
            try:
                await adapter.send_text(message)
                log.notifications.debug(
                    "[notifications] deliverer._transport: sent",
                    extra={"_fields": {"channel": channel, "attempt": attempt}},
                )
                return "delivered"
            except Exception as exc:  # B5 — transient/permanent send failure
                if attempt == 1:
                    log.notifications.warning(
                        "[notifications] deliverer._transport: send failed — retrying once",
                        exc_info=exc,
                        extra={"_fields": {"channel": channel, "attempt": attempt}},
                    )
                    continue
                log.notifications.error(
                    "[notifications] deliverer._transport: send failed after retry",
                    exc_info=exc,
                    extra={"_fields": {"channel": channel, "attempt": attempt}},
                )
                return "failed"
        return "failed"  # pragma: no cover — loop always returns

    def _log_exit(self, status: DeliveryStatus, channel: str, t0: float) -> None:
        # 4. EXIT
        duration_ms = (_time.monotonic() - t0) * 1000
        log.notifications.debug(
            "[notifications] deliverer.deliver: exit",
            extra={
                "_fields": {
                    "status": status,
                    "channel": channel,
                    "duration_ms": duration_ms,
                }
            },
        )
