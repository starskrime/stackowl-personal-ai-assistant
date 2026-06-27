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
#
# It is currently EMPTY — and this is an INTENTIONAL, REASONED deferral (F-78),
# not an oversight:
#   * ``website_watch.changed`` is delivered via the DURABLE exactly-once seam
#     (the handler calls ``ProactiveJobDeliverer.deliver_for_job`` directly), not
#     this unledgered bridge — so it is no longer routed here (WS-D).
#   * ``perch.file_landed`` is dead v1 vocabulary — no module/emitter exists.
#
# WHY no other already-published event can be wired here WITHOUT a publisher
# change: the deliver seam this bridge funnels through (see
# :meth:`EventDeliveryBridge._build_notification`) requires every event payload
# to carry a non-``message`` body AND an explicit channel-native ``target`` — the
# "honest recipient" C1 invariant forbids guessing the recipient (no ``_last_*``
# fallback). The genuinely-proactive events that ARE published today carry domain
# payloads with NEITHER:
#   * ``budget_exceeded`` / ``budget_80pct_alert`` (cost_tracker) — payload is
#     ``{"current_usd": ..., "limit_usd": ...}`` (no message, no target). These
#     have no bus subscriber at all, so a real proactive gap exists, BUT they are
#     owner-GLOBAL alerts with no per-event recipient.
#   * ``parliament.completed`` (orchestrator) — payload is a bare ``session_id``.
# Routing any of these through this seam UNCHANGED would drop every event at the
# recipient rail. The honest UNBLOCK contract: a publisher must include a
# resolvable channel-native ``target`` (and a ``message``) in its payload — or
# route via the durable exactly-once seam, as ``website_watch`` does. Until then
# the bridge stays dormant (registers no subscriptions, logs a clean state) and
# the machinery is kept intact + unit-tested for that future event.
_ALLOWED_EVENTS: frozenset[str] = frozenset()
# Concrete proactive-event candidates that exist today but CANNOT ride this seam
# unchanged (see the rationale above). Tracked as a named constant so the
# deferral is explicit + regression-pinned rather than an undocumented gap.
_DEFERRED_PROACTIVE_CANDIDATES: frozenset[str] = frozenset(
    {"budget_exceeded", "budget_80pct_alert", "parliament.completed"}
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
        """Subscribe this bridge's async handler for every allow-listed event.

        With an EMPTY allow-list (the current state — see ``_ALLOWED_EVENTS``) this
        is a clean DORMANT no-op: nothing is subscribed and the log says so plainly
        rather than claiming "subscribed N events".
        """
        log.notifications.debug(
            "[notifications] event_bridge.register: entry",
            extra={"_fields": {"events": sorted(_ALLOWED_EVENTS)}},
        )
        if not _ALLOWED_EVENTS:
            log.notifications.info(
                "[notifications] event_bridge.register: no proactive bus events to "
                "subscribe — bridge dormant (durable seams handle proactivity)",
            )
            return
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
