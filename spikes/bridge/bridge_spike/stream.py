"""One cursor-numbered event source shared by both carriers (AD-31): a
synthetic-but-realistic fixture (no real recorded-platform-event journal
exists yet -- see this story's spec Design Notes), replayed at recorded
rates including a deliberate burst, fanned out to every connected client
through a bounded per-client queue that never blocks the replayer and
resyncs a slow client instead of losing it (NFR11, NFR13, NFR46, AD-38).

`stream_for_client()` is the ONE async generator both `server.py`'s SSE
route and `webtransport_server.py`'s WebTransport session drive: neither
carrier reimplements resume-from-cursor, heartbeats, or overflow handling
on its own -- "one envelope family from one cursor-numbered event source".
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import time
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass

DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 2.0
DEFAULT_CLIENT_QUEUE_MAXSIZE = 64

# Bounds StreamHub._recorded so a long-running kit process's history doesn't
# grow forever -- generous enough that any realistic resume-from-cursor gap
# (backgrounding, app-switch, a network drop) is still covered, while
# keeping the kit's own memory footprint from climbing without limit over
# days of uptime.
DEFAULT_MAX_RECORDED_EVENTS = 10_000


@dataclass(frozen=True)
class FixtureEvent:
    """One entry in the synthetic fixture. `delay_seconds` is the gap since
    the PREVIOUS fixture event at the recorded rate -- consecutive entries
    with `delay_seconds == 0.0` form the fixture's deliberate burst."""

    kind: str
    intensity: float
    rendering: str
    delay_seconds: float


# A small, deterministic, mixed-kind fixture sanitised to metadata only
# (id/kind/intensity/rendering -- the same shape push.py's build_metadata
# already established for FR26 "never full content"), with one deliberate
# five-event burst in the middle: this exercises steady recorded-rate
# pacing AND a burst in the same cycle, as the AC requires.
FIXTURE: tuple[FixtureEvent, ...] = (
    FixtureEvent("progress", 0.10, "Something started", 0.0),
    FixtureEvent("progress", 0.35, "Still working", 0.6),
    FixtureEvent("question", 0.50, "Needs an answer", 0.4),
    FixtureEvent("alert", 0.80, "Burst item 1", 1.0),
    FixtureEvent("alert", 0.81, "Burst item 2", 0.0),
    FixtureEvent("alert", 0.82, "Burst item 3", 0.0),
    FixtureEvent("alert", 0.83, "Burst item 4", 0.0),
    FixtureEvent("alert", 0.84, "Burst item 5", 0.0),
    FixtureEvent("progress", 0.20, "Cooling down", 0.5),
    FixtureEvent("question", 0.15, "One more thing", 0.3),
)


@dataclass(frozen=True)
class RecordedEvent:
    cursor: int
    kind: str
    id: str
    intensity: float
    rendering: str


def envelope(event: RecordedEvent) -> dict:
    """The one envelope shape both carriers ever send for a stream event."""
    return {
        "type": "event",
        "cursor": event.cursor,
        "id": event.id,
        "kind": event.kind,
        "intensity": event.intensity,
        "rendering": event.rendering,
    }


def heartbeat_envelope(head_cursor: int) -> dict:
    return {"type": "heartbeat", "head_cursor": head_cursor}


def resync_envelope(cursor: int) -> dict:
    return {"type": "resync", "cursor": cursor}


def hello_envelope(cursor: int, heartbeat_interval_seconds: float) -> dict:
    """Sent once, immediately, on every (re)connect -- the "announced
    interval" NFR11 calls for, plus the cursor the client was actually
    resumed from (equal to `head_cursor` when nothing was missed)."""
    return {"type": "hello", "cursor": cursor, "heartbeat_interval_seconds": heartbeat_interval_seconds}


class ClientQueue:
    """A bounded outbound queue for one client's carrier session. `push()`
    is synchronous and never blocks: once `maxsize` is reached, everything
    currently queued is dropped and the client is marked for a `resync`
    instead of being handed a hole in the middle of its stream -- the
    replayer/fan-out (`StreamHub._record`) is never slowed down or blocked
    by one slow client (NFR46, AD-38)."""

    def __init__(self, maxsize: int = DEFAULT_CLIENT_QUEUE_MAXSIZE) -> None:
        self._maxsize = maxsize
        self._items: deque[dict] = deque()
        self._overflowed = False
        self._event = asyncio.Event()

    def push(self, item: dict) -> None:
        if len(self._items) >= self._maxsize:
            self._items.clear()
            self._overflowed = True
        self._items.append(item)
        self._event.set()

    async def wait(self, timeout: float | None = None) -> bool:
        """True once an item has arrived; False if `timeout` elapses first."""
        try:
            await asyncio.wait_for(self._event.wait(), timeout=timeout)
            return True
        except TimeoutError:
            return False

    def drain(self) -> tuple[list[dict], bool]:
        """Returns (queued envelopes, whether this client overflowed since
        the last drain) and clears both."""
        items = list(self._items)
        self._items.clear()
        overflowed = self._overflowed
        self._overflowed = False
        self._event.clear()
        return items, overflowed


class StreamHub:
    """The one cursor-numbered event source both carriers stream from
    (AD-31). Owns the synthetic replayer and fans every newly recorded
    event out to every connected client's bounded queue -- recording never
    blocks on a slow client (`_record` only ever calls the synchronous,
    non-blocking `ClientQueue.push`)."""

    def __init__(
        self,
        fixture: tuple[FixtureEvent, ...] = FIXTURE,
        *,
        heartbeat_interval_seconds: float = DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
        client_queue_maxsize: int = DEFAULT_CLIENT_QUEUE_MAXSIZE,
        max_recorded_events: int = DEFAULT_MAX_RECORDED_EVENTS,
    ) -> None:
        self._fixture = fixture
        self.heartbeat_interval_seconds = heartbeat_interval_seconds
        self._client_queue_maxsize = client_queue_maxsize
        self._recorded: deque[RecordedEvent] = deque(maxlen=max_recorded_events)
        self._clients: dict[int, ClientQueue] = {}
        self._next_client_id = itertools.count(1)
        self._replay_task: asyncio.Task[None] | None = None

    @property
    def head_cursor(self) -> int:
        return self._recorded[-1].cursor if self._recorded else 0

    def events_after(self, cursor: int) -> list[RecordedEvent]:
        """Every recorded event strictly after `cursor` -- the gap-fill a
        resuming client needs, read directly from already-recorded history
        (independent of live fan-out timing, so it can never miss an event
        a slow-to-register client would otherwise have raced)."""
        return [event for event in self._recorded if event.cursor > cursor]

    def register_client(self) -> tuple[int, ClientQueue]:
        client_id = next(self._next_client_id)
        queue = ClientQueue(maxsize=self._client_queue_maxsize)
        self._clients[client_id] = queue
        return client_id, queue

    def unregister_client(self, client_id: int) -> None:
        self._clients.pop(client_id, None)

    def _record(self, event: RecordedEvent) -> None:
        self._recorded.append(event)
        for queue in self._clients.values():
            queue.push(envelope(event))

    def record_now(self, *, kind: str, intensity: float, rendering: str) -> RecordedEvent:
        """Appends one event at the next cursor, right now."""
        cursor = self.head_cursor + 1
        event = RecordedEvent(cursor=cursor, kind=kind, id=f"stream-{cursor}", intensity=intensity, rendering=rendering)
        self._record(event)
        return event

    async def _run_replay(self) -> None:
        """Cycles the fixture forever at its recorded rate (each event's
        `delay_seconds` after the previous one), including the fixture's
        deliberate burst -- fresh cursors every cycle, simulating a live
        platform that keeps producing events for as long as the kit runs."""
        while True:
            for fixture_event in self._fixture:
                if fixture_event.delay_seconds > 0:
                    await asyncio.sleep(fixture_event.delay_seconds)
                self.record_now(
                    kind=fixture_event.kind, intensity=fixture_event.intensity, rendering=fixture_event.rendering
                )

    def start(self) -> None:
        """Starts the replayer, if it is not already running. Idempotent so
        callers (kit.py's `_serve()`, tests) never have to track whether
        they already called it."""
        if self._replay_task is None:
            self._replay_task = asyncio.ensure_future(self._run_replay())

    async def stop(self) -> None:
        if self._replay_task is not None:
            self._replay_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._replay_task
            self._replay_task = None


async def stream_for_client(hub: StreamHub, last_cursor: int) -> AsyncIterator[dict]:
    """The one async generator both carriers drive. Yields, in order:

    1. a `hello` envelope, immediately (announces the heartbeat interval);
    2. every recorded event after `last_cursor` (the resume-from-cursor
       gap-fill -- NFR13);
    3. live events as they are recorded, deduplicated against what the
       gap-fill already covered so a race between the two never double-sends;
    4. a `heartbeat` envelope carrying `head_cursor` whenever the announced
       interval elapses with nothing else to send (NFR11);
    5. a `resync` envelope, instead of stale queued events, the moment this
       client's own bounded queue overflows (NFR46, AD-38).

    Exits only when the caller stops iterating (a real disconnect) or
    cancels the enclosing task; `finally` always unregisters the client so
    a dropped connection can never leak a queue.
    """
    client_id, queue = hub.register_client()
    try:
        backfill = hub.events_after(last_cursor)
        last_sent_cursor = last_cursor
        yield hello_envelope(hub.head_cursor, hub.heartbeat_interval_seconds)
        for event in backfill:
            yield envelope(event)
            last_sent_cursor = event.cursor
        last_heartbeat = time.monotonic()
        while True:
            remaining = hub.heartbeat_interval_seconds - (time.monotonic() - last_heartbeat)
            arrived = await queue.wait(timeout=max(remaining, 0))
            if not arrived:
                yield heartbeat_envelope(hub.head_cursor)
                last_heartbeat = time.monotonic()
                continue
            items, overflowed = queue.drain()
            if overflowed:
                # The client's queue was cleared mid-flight -- resync to
                # whatever the head is RIGHT NOW (not the state at overflow
                # time), so the client's next reconnect/backfill is provably
                # complete with no further race window.
                resync_cursor = hub.head_cursor
                yield resync_envelope(resync_cursor)
                last_sent_cursor = resync_cursor
                last_heartbeat = time.monotonic()
                continue
            for item in items:
                if item["cursor"] <= last_sent_cursor:
                    continue  # already covered by the initial backfill
                yield item
                last_sent_cursor = item["cursor"]
    finally:
        hub.unregister_client(client_id)


__all__ = [
    "DEFAULT_CLIENT_QUEUE_MAXSIZE",
    "DEFAULT_HEARTBEAT_INTERVAL_SECONDS",
    "ClientQueue",
    "FIXTURE",
    "FixtureEvent",
    "RecordedEvent",
    "StreamHub",
    "envelope",
    "heartbeat_envelope",
    "hello_envelope",
    "resync_envelope",
    "stream_for_client",
]
