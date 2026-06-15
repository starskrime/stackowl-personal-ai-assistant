"""C1 / F105 — event-driven proactivity bridge through the SAME deliver seam.

Publishing an allow-listed proactive event drives a proactive notification through
``proactive_deliverer.deliver`` — the SAME single seam the cron path uses, never a
parallel send path. The EventBus is upgraded to run coroutine handlers on the
running loop with per-handler error isolation (a raising subscriber is logged, the
emitter never blocks on network I/O).
"""

from __future__ import annotations

import asyncio

import pytest

from stackowl.events.bus import EventBus
from stackowl.notifications.event_bridge import EventDeliveryBridge

pytestmark = pytest.mark.asyncio


class _RecordingDeliverer:
    def __init__(self) -> None:
        self.delivered: list[object] = []

    async def deliver(self, notification: object) -> str:
        self.delivered.append(notification)
        return "delivered"


async def test_event_drives_one_deliver_call() -> None:
    bus = EventBus()
    deliverer = _RecordingDeliverer()
    bridge = EventDeliveryBridge(deliverer=deliverer)  # type: ignore[arg-type]
    bridge.register(bus)

    bus.emit(
        "website_watch.changed",
        {"message": "site changed", "channel": "telegram", "target": 12345},
    )
    # Coroutine handler is scheduled on the loop — let it run.
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert len(deliverer.delivered) == 1, "exactly one deliver() through the seam"
    notif = deliverer.delivered[0]
    assert getattr(notif, "message", None) == "site changed"
    assert getattr(notif, "target", None) == 12345
    assert getattr(notif, "channel_name", None) == "telegram"


async def test_non_allowlisted_event_is_ignored() -> None:
    """An internal/telemetry event must NOT ping the user (allow-list only)."""
    bus = EventBus()
    deliverer = _RecordingDeliverer()
    bridge = EventDeliveryBridge(deliverer=deliverer)  # type: ignore[arg-type]
    bridge.register(bus)

    bus.emit("some.internal.telemetry", {"message": "noise", "channel": "telegram"})
    await asyncio.sleep(0)

    assert deliverer.delivered == [], "non-allow-listed events never deliver"


async def test_missing_recipient_event_does_not_deliver() -> None:
    """An allow-listed event with no resolvable recipient does not send (honest)."""
    bus = EventBus()
    deliverer = _RecordingDeliverer()
    bridge = EventDeliveryBridge(deliverer=deliverer)  # type: ignore[arg-type]
    bridge.register(bus)

    bus.emit("website_watch.changed", {"message": "changed", "channel": "telegram"})
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert deliverer.delivered == [], "no target -> no deliver (no _last_* guess)"


async def test_sync_handler_still_runs_backcompat() -> None:
    """The async upgrade is additive — existing sync subscribers still fire inline."""
    bus = EventBus()
    seen: list[object] = []
    bus.subscribe("settings_reloaded", lambda payload: seen.append(payload))
    bus.emit("settings_reloaded", {"k": "v"})
    assert seen == [{"k": "v"}]
