"""EventDeliveryBridge — one event->notification subscriber on the deliver seam (F105).

The :class:`EventBus` had effectively one subscriber and ``morning_brief`` emitted
to nobody, so StackOwl had no event-driven proactivity. This bridge subscribes a
small ALLOW-LIST of proactive event names and, for each, builds a
:class:`Notification` and calls the SAME ``proactive_deliverer.deliver`` seam the
cron path uses — never a parallel send path.

Hard rails (C1 invariants):

* **Allow-list only** — an internal/telemetry event must never ping the user.
* **Honest recipient** — an event with no resolvable channel-native ``target`` is
  NOT delivered (no ``_last_*`` guess); it is logged and dropped.
* **Backpressure-safe** — a bounded semaphore coalesces event bursts so a flood
  cannot launch unbounded concurrent sends; overflow drops the excess (logged),
  never blocks the emitter.
* **Per-subscriber error isolation** — the bus done-callback logs any task
  exception; the bridge additionally guards its own build/deliver (B5).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from stackowl.infra.observability import log
from stackowl.notifications.router import Notification

if TYPE_CHECKING:  # pragma: no cover — typing-only imports
    from stackowl.events.bus import EventBus
    from stackowl.notifications.deliverer import ProactiveDeliverer

# Allow-list of proactive event names a user PING is permitted for. Internal /
# telemetry events (e.g. ``morning_brief_rendered``, ``settings_reloaded``) are
# deliberately ABSENT — they must never drive a user-facing send.
_ALLOWED_EVENTS = frozenset(
    {
        "website_watch.changed",
        "perch.file_landed",
    }
)
_DEFAULT_CATEGORY = "proactive_event"
# Bound concurrent event-driven sends so an event burst cannot fan out unbounded.
_MAX_INFLIGHT = 16


class EventDeliveryBridge:
    """Subscribes proactive events and funnels them through the deliver seam."""

    def __init__(
        self,
        deliverer: ProactiveDeliverer,
        *,
        max_inflight: int = _MAX_INFLIGHT,
    ) -> None:
        self._deliverer = deliverer
        self._max_inflight = max_inflight
        self._inflight = 0

    def register(self, bus: EventBus) -> None:
        """Subscribe this bridge's async handler for every allow-listed event."""
        log.notifications.debug(
            "[notifications] event_bridge.register: entry",
            extra={"_fields": {"events": sorted(_ALLOWED_EVENTS)}},
        )
        for event in _ALLOWED_EVENTS:
            bus.subscribe(event, self._on_event)
        log.notifications.info(
            "[notifications] event_bridge.register: subscribed proactive events",
            extra={"_fields": {"count": len(_ALLOWED_EVENTS)}},
        )

    async def _on_event(self, payload: Any) -> None:
        """Build a :class:`Notification` from the event and deliver it. Never raises.

        Backpressure: a saturated semaphore (too many in-flight sends) DROPS the
        event (logged) rather than queueing unboundedly or blocking the loop.
        """
        # 1. ENTRY
        log.notifications.debug(
            "[notifications] event_bridge._on_event: entry",
            extra={"_fields": {"payload_type": type(payload).__name__}},
        )
        notification = self._build_notification(payload)
        if notification is None:
            return  # already logged — unresolved recipient or malformed payload

        # 2. DECISION — backpressure: refuse to exceed the in-flight bound. The
        # counter is mutated only on the single event loop (no lock needed); an
        # over-bound event is DROPPED (logged), never queued unboundedly.
        if self._inflight >= self._max_inflight:
            log.notifications.warning(
                "[notifications] event_bridge._on_event: in-flight bound reached "
                "— dropping event (backpressure)",
                extra={
                    "_fields": {
                        "channel": notification.channel_name,
                        "inflight": self._inflight,
                    }
                },
            )
            return
        self._inflight += 1
        try:
            # 3. STEP — the SINGLE deliver seam (shared with the cron path).
            status = await self._deliverer.deliver(notification)
        except Exception as exc:  # B5 — deliverer is no-raise, but belt-and-braces
            log.notifications.error(
                "[notifications] event_bridge._on_event: deliver failed",
                exc_info=exc,
                extra={"_fields": {"channel": notification.channel_name}},
            )
            return
        finally:
            self._inflight -= 1
        # 4. EXIT
        log.notifications.info(
            "[notifications] event_bridge._on_event: exit",
            extra={
                "_fields": {"channel": notification.channel_name, "status": status}
            },
        )

    def _build_notification(self, payload: Any) -> Notification | None:
        """Build a :class:`Notification` from an event payload, or ``None``.

        Returns ``None`` (logged) when the payload has no message OR no resolvable
        channel-native recipient — an event with no honest destination is dropped,
        never delivered to whoever messaged last (no ``_last_*`` guess).
        """
        if not isinstance(payload, dict):
            log.notifications.warning(
                "[notifications] event_bridge._build_notification: non-dict payload "
                "— dropping",
                extra={"_fields": {"payload_type": type(payload).__name__}},
            )
            return None
        message = payload.get("message")
        channel = payload.get("channel")
        target = payload.get("target")
        if not message:
            log.notifications.warning(
                "[notifications] event_bridge._build_notification: no message — drop",
            )
            return None
        if target is None:
            log.notifications.warning(
                "[notifications] event_bridge._build_notification: no resolvable "
                "recipient — dropping (no _last_* guess)",
                extra={"_fields": {"channel": channel}},
            )
            return None
        return Notification(
            message=str(message),
            urgency="normal",
            category=str(payload.get("category", _DEFAULT_CATEGORY)),
            channel_name=channel,
            target=target,
        )
