"""Unit tests for bridge_spike.stream: cursor numbering, replay-at-rate
including a burst, heartbeat `head_cursor`, and bounded-queue
overflow -> `resync` (NFR11, NFR13, NFR46, AD-31, AD-38)."""

from __future__ import annotations

import asyncio
import time

from bridge_spike import stream as stream_module


async def test_cursors_increase_by_one_and_never_repeat() -> None:
    hub = stream_module.StreamHub()
    first = hub.record_now(kind="progress", intensity=0.1, rendering="a")
    second = hub.record_now(kind="alert", intensity=0.2, rendering="b")
    third = hub.record_now(kind="question", intensity=0.3, rendering="c")
    assert (first.cursor, second.cursor, third.cursor) == (1, 2, 3)
    assert hub.head_cursor == 3


async def test_events_after_returns_only_the_gap() -> None:
    hub = stream_module.StreamHub()
    for i in range(5):
        hub.record_now(kind="progress", intensity=float(i), rendering=str(i))
    gap = hub.events_after(2)
    assert [e.cursor for e in gap] == [3, 4, 5]


async def test_stream_for_client_yields_hello_then_backfill_then_live() -> None:
    hub = stream_module.StreamHub(heartbeat_interval_seconds=10)
    hub.record_now(kind="progress", intensity=0.1, rendering="already-here")

    generator = stream_module.stream_for_client(hub, 0)
    hello = await generator.__anext__()
    assert hello["type"] == "hello"
    assert hello["cursor"] == 1

    backfilled = await generator.__anext__()
    assert backfilled["type"] == "event"
    assert backfilled["cursor"] == 1

    hub.record_now(kind="alert", intensity=0.9, rendering="live-one")
    live = await generator.__anext__()
    assert live["type"] == "event"
    assert live["cursor"] == 2

    await generator.aclose()


async def test_resume_from_cursor_replays_the_gap_with_no_loss_or_duplicates() -> None:
    hub = stream_module.StreamHub(heartbeat_interval_seconds=10)
    for i in range(1, 6):
        hub.record_now(kind="progress", intensity=float(i), rendering=str(i))

    # Resuming from cursor 2 (as if this client had already seen 1 and 2,
    # then dropped) must replay exactly 3, 4, 5 -- no loss, no duplicates.
    generator = stream_module.stream_for_client(hub, 2)
    hello = await generator.__anext__()
    assert hello["cursor"] == 5
    seen_cursors = []
    for _ in range(3):
        item = await generator.__anext__()
        assert item["type"] == "event"
        seen_cursors.append(item["cursor"])
    assert seen_cursors == [3, 4, 5]
    await generator.aclose()


async def test_replay_at_recorded_rate_including_a_burst() -> None:
    """A short custom fixture: two spaced-out events, then a three-event
    burst (delay 0) -- the spaced events must be measurably separated in
    wall-clock time, and the burst events must land close together."""
    fixture = (
        stream_module.FixtureEvent("progress", 0.1, "one", 0.0),
        stream_module.FixtureEvent("progress", 0.2, "two", 0.15),
        stream_module.FixtureEvent("alert", 0.3, "burst-1", 0.15),
        stream_module.FixtureEvent("alert", 0.4, "burst-2", 0.0),
        stream_module.FixtureEvent("alert", 0.5, "burst-3", 0.0),
    )
    hub = stream_module.StreamHub(fixture=fixture, heartbeat_interval_seconds=30)
    hub.start()
    try:
        timestamps: dict[int, float] = {}
        deadline = time.monotonic() + 3
        while len(timestamps) < 5 and time.monotonic() < deadline:
            for event in hub.events_after(max(timestamps.keys(), default=0)):
                if len(timestamps) >= 5:
                    break  # the fixture loops forever -- ignore the next cycle's events
                timestamps[event.cursor] = time.monotonic()
            await asyncio.sleep(0.02)
        assert len(timestamps) == 5

        # one -> two: spaced by ~0.15s, measurably slower than the burst gap.
        spaced_gap = timestamps[2] - timestamps[1]
        # burst-1 -> burst-2 -> burst-3: delay_seconds == 0, should land
        # far closer together than the spaced gap above.
        burst_gap_1 = timestamps[4] - timestamps[3]
        burst_gap_2 = timestamps[5] - timestamps[4]
        assert spaced_gap > burst_gap_1
        assert spaced_gap > burst_gap_2
    finally:
        await hub.stop()


async def test_heartbeat_carries_head_cursor_at_the_announced_interval() -> None:
    hub = stream_module.StreamHub(heartbeat_interval_seconds=0.05)
    hub.record_now(kind="progress", intensity=0.1, rendering="only-one")

    generator = stream_module.stream_for_client(hub, 0)
    hello = await generator.__anext__()
    assert hello["heartbeat_interval_seconds"] == 0.05
    backfilled = await generator.__anext__()
    assert backfilled["cursor"] == 1

    # No more events are ever recorded -- the next item MUST be a heartbeat,
    # carrying the current head_cursor, within roughly the announced interval.
    started = time.monotonic()
    heartbeat = await asyncio.wait_for(generator.__anext__(), timeout=1)
    elapsed = time.monotonic() - started
    assert heartbeat["type"] == "heartbeat"
    assert heartbeat["head_cursor"] == 1
    assert elapsed < 0.5
    await generator.aclose()


async def test_client_queue_overflow_triggers_resync_not_loss() -> None:
    hub = stream_module.StreamHub(heartbeat_interval_seconds=30, client_queue_maxsize=3)
    generator = stream_module.stream_for_client(hub, 0)
    await generator.__anext__()  # hello -- registers the client's queue

    # Flood well past the bounded queue's capacity without ever draining --
    # the replayer/fan-out (record_now -> ClientQueue.push) must never block.
    for i in range(1, 11):
        hub.record_now(kind="alert", intensity=float(i), rendering=str(i))

    item = await generator.__anext__()
    assert item["type"] == "resync"
    assert item["cursor"] == hub.head_cursor
    await generator.aclose()


async def test_client_queue_push_never_blocks_past_maxsize() -> None:
    queue = stream_module.ClientQueue(maxsize=2)
    for i in range(50):
        queue.push({"type": "event", "cursor": i})  # must return immediately every time
    items, overflowed = queue.drain()
    assert overflowed is True
    assert len(items) <= 2


async def test_unregistering_a_client_stops_further_fan_out() -> None:
    hub = stream_module.StreamHub()
    client_id, queue = hub.register_client()
    hub.record_now(kind="progress", intensity=0.1, rendering="before")
    items_before, _ = queue.drain()
    assert len(items_before) == 1

    hub.unregister_client(client_id)
    hub.record_now(kind="progress", intensity=0.2, rendering="after")
    items_after, _ = queue.drain()
    assert items_after == []
