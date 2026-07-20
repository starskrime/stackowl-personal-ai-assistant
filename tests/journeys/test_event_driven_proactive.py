"""C1 / F105 — event-driven proactivity bridge through the SAME deliver seam.

The :class:`EventDeliveryBridge` funnels any genuinely bus-native proactive event
through ``proactive_deliverer.deliver`` — the SAME single seam the cron path uses,
never a parallel send path. The class is kept intact for future bus-native events.

WS-D update: ``website_watch.changed`` is now delivered via the DURABLE
exactly-once seam (the handler calls ``ProactiveJobDeliverer.deliver_for_job``
directly), NOT this unledgered bridge — so it is removed from the allow-list. The
v1 ``perch.file_landed`` event has no emitter anywhere and is removed too.

FX-10 update: ``budget_exceeded``/``budget_80pct_alert`` are UNBLOCKED —
CostTracker now attaches ``message``/``channel``/``target`` to the payload when
a recipient resolves (see providers/cost_tracker.py, startup/orchestrator.py),
so they ride this seam for real. ``parliament.completed`` stays deferred (see
``_DEFERRED_PROACTIVE_CANDIDATES``'s own comment for why). These tests pin the
allow-list's exact membership and the still-intact deliver mechanics.
"""

from __future__ import annotations

import asyncio

import pytest

from stackowl.events.bus import EventBus
from stackowl.notifications.event_bridge import (
    _ALLOWED_EVENTS,
    _DEFERRED_PROACTIVE_CANDIDATES,
    EventDeliveryBridge,
)

pytestmark = pytest.mark.asyncio


class _RecordingDeliverer:
    def __init__(self) -> None:
        self.delivered: list[object] = []

    async def deliver(self, notification: object) -> str:
        self.delivered.append(notification)
        return "delivered"


class _RecordingBus(EventBus):
    """An EventBus that records every subscribe() so we can assert subscriptions."""

    def __init__(self) -> None:
        super().__init__()
        self.subscribed_events: list[str] = []

    def subscribe(self, event: str, handler: object) -> None:  # type: ignore[override]
        self.subscribed_events.append(event)
        super().subscribe(event, handler)  # type: ignore[arg-type]


async def test_website_watch_changed_is_not_a_bridge_event() -> None:
    """website_watch.changed is delivered via the durable seam, not the bridge."""
    assert "website_watch.changed" not in _ALLOWED_EVENTS


async def test_perch_file_landed_is_removed_dead_vocabulary() -> None:
    """perch.file_landed has no emitter anywhere — it must not be subscribed."""
    assert "perch.file_landed" not in _ALLOWED_EVENTS


async def test_proactive_candidates_are_intentionally_deferred_not_subscribed() -> None:
    """FX-10: ``parliament.completed`` stays DELIBERATELY off the allow-list —
    its payload is a bare session_id (not a dict), and unlike the now-unblocked
    budget alerts it has no single obvious recipient (see the deferral comment
    at ``_DEFERRED_PROACTIVE_CANDIDATES``). This pins that as an explicit,
    reasoned decision rather than an undocumented gap."""
    assert _DEFERRED_PROACTIVE_CANDIDATES, "the deferral rationale must name candidates"
    # None of the deferred candidates may be silently subscribed.
    assert _DEFERRED_PROACTIVE_CANDIDATES.isdisjoint(_ALLOWED_EVENTS)


async def test_allowlist_subscribes_exactly_the_unblocked_budget_events() -> None:
    """FX-10: budget_exceeded/budget_80pct_alert are the only production events
    on the allow-list today; registering subscribes exactly those two."""
    bus = _RecordingBus()
    deliverer = _RecordingDeliverer()
    bridge = EventDeliveryBridge(deliverer=deliverer)  # type: ignore[arg-type]

    bridge.register(bus)  # must not raise

    assert set(bus.subscribed_events) == {"budget_exceeded", "budget_80pct_alert"}


async def test_non_allowlisted_event_is_ignored() -> None:
    """An internal/telemetry event must NOT ping the user (allow-list only)."""
    bus = EventBus()
    deliverer = _RecordingDeliverer()
    bridge = EventDeliveryBridge(deliverer=deliverer)  # type: ignore[arg-type]
    bridge.register(bus)

    bus.emit("some.internal.telemetry", {"message": "noise", "channel": "telegram"})
    await asyncio.sleep(0)

    assert deliverer.delivered == [], "non-allow-listed events never deliver"


async def test_bridge_deliver_mechanics_still_intact() -> None:
    """The class is kept for future bus-native events — its deliver path works.

    Manually wire the bridge to an arbitrary event and assert one deliver()
    through the seam with the channel-native target preserved. (No event is on
    the production allow-list today, but the mechanism must remain correct.)"""
    bus = EventBus()
    deliverer = _RecordingDeliverer()
    bridge = EventDeliveryBridge(deliverer=deliverer)  # type: ignore[arg-type]
    bus.subscribe("future.bus_native", bridge._on_event)

    bus.emit(
        "future.bus_native",
        {"message": "hello", "channel": "telegram", "target": 12345},
    )
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert len(deliverer.delivered) == 1
    notif = deliverer.delivered[0]
    assert getattr(notif, "message", None) == "hello"
    assert getattr(notif, "target", None) == 12345
    assert getattr(notif, "channel_name", None) == "telegram"


async def test_budget_exceeded_delivers_when_recipient_resolved() -> None:
    """FX-10 end-to-end: a budget_exceeded event WITH message/channel/target
    (the shape CostTracker now emits when a recipient is resolved) actually
    reaches the deliverer through the real allow-list + bridge, not a stub."""
    bus = EventBus()
    deliverer = _RecordingDeliverer()
    bridge = EventDeliveryBridge(deliverer=deliverer)  # type: ignore[arg-type]
    bridge.register(bus)

    bus.emit(
        "budget_exceeded",
        {
            "current_usd": 12.5, "limit_usd": 10.0,
            "message": "Daily LLM budget exceeded: $12.50 / $10.00",
            "channel": "telegram", "target": 12345,
        },
    )
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert len(deliverer.delivered) == 1
    notif = deliverer.delivered[0]
    assert getattr(notif, "target", None) == 12345
    assert "12.50" in getattr(notif, "message", "")


async def test_budget_exceeded_dropped_when_no_recipient_resolved() -> None:
    """FX-10: the unchanged, unresolved-recipient case (no target) is still
    honestly dropped, not guessed — CostTracker's own default when
    resolve_owner_addresses finds no single owner."""
    bus = EventBus()
    deliverer = _RecordingDeliverer()
    bridge = EventDeliveryBridge(deliverer=deliverer)  # type: ignore[arg-type]
    bridge.register(bus)

    bus.emit("budget_exceeded", {"current_usd": 12.5, "limit_usd": 10.0})
    await asyncio.sleep(0)

    assert deliverer.delivered == []


async def test_sync_handler_still_runs_backcompat() -> None:
    """The async upgrade is additive — existing sync subscribers still fire inline."""
    bus = EventBus()
    seen: list[object] = []
    bus.subscribe("settings_reloaded", lambda payload: seen.append(payload))
    bus.emit("settings_reloaded", {"k": "v"})
    assert seen == [{"k": "v"}]
