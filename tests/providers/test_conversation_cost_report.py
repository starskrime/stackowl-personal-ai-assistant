"""DEBT-7 part 2 — report what a conversation cost, at the moment it ends.

Bakir chose per-conversation reporting over a daily threshold (2026-07-26):
"what did that conversation cost" is the question he actually has. This rides
D01.7's ``session.rollover`` seam rather than adding a scheduler — one
boundary, many consumers, which is dedup target X3's principle. The rollover
summary consumer in memory/rollover_summary_handler.py is the sibling that
established the pattern, including its rule that a consumer must NEVER break
the boundary.

``cost_records`` already carries ``session_key`` and ``conversation_id`` (D01.6 /
D01.7), so the aggregate is a query, not a migration.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

from stackowl.db.pool import DbPool
from stackowl.events.bus import EventBus
from stackowl.providers.conversation_cost_report import (
    COST_REPORT_EVENT,
    register_conversation_cost_consumer,
)
from stackowl.providers.cost_tracker import CostTracker
from stackowl.sessions.store import SessionStore


async def _wait_for(predicate: Callable[[], bool], timeout: float = 3.0) -> bool:
    """Async handlers are SCHEDULED as background tasks by EventBus.emit, so a
    bare sleep(0) would be a race. Poll the condition instead."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return predicate()


async def _record(tracker: CostTracker, *, conversation_id: str, trace_id: str,
                  session_key: str = "owl:secretary:telegram:dm:1") -> None:
    await tracker.record(
        provider_name="acme", model="acme-v1",
        input_tokens=1000, output_tokens=100, duration_ms=1.0,
        trace_id=trace_id, session_key=session_key, conversation_id=conversation_id,
    )


async def test_session_total_sums_only_that_incarnation(tmp_db: DbPool) -> None:
    """A lane OUTLIVES its incarnations, so cost must be scoped to both —
    session_key alone would bill a fresh conversation for its predecessor's
    spend, which is the same scope-confusion class D01.7 kept hitting."""
    tracker = CostTracker(db=tmp_db, event_bus=EventBus(), daily_limit_usd=None)
    await _record(tracker, conversation_id="INC_A", trace_id="t1")
    await _record(tracker, conversation_id="INC_B", trace_id="t2")

    summary = await tracker.session_total("owl:secretary:telegram:dm:1", "INC_A")

    assert summary.call_count == 1
    assert summary.total_usd > 0


async def test_session_total_is_zero_for_an_unknown_incarnation(tmp_db: DbPool) -> None:
    tracker = CostTracker(db=tmp_db, event_bus=EventBus(), daily_limit_usd=None)

    summary = await tracker.session_total("owl:secretary:telegram:dm:1", "NOPE")

    assert summary.call_count == 0
    assert summary.total_usd == 0.0


async def test_rollover_reports_what_the_conversation_cost(tmp_db: DbPool) -> None:
    bus = EventBus()
    tracker = CostTracker(db=tmp_db, event_bus=bus, daily_limit_usd=None)
    await _record(tracker, conversation_id="INC_A", trace_id="t1")
    seen: list[dict] = []
    bus.subscribe(COST_REPORT_EVENT, lambda payload: seen.append(payload))
    register_conversation_cost_consumer(bus, tracker)

    bus.emit(SessionStore.ROLLOVER_EVENT, {
        "session_key": "owl:secretary:telegram:dm:1",
        "old_conversation_id": "INC_A",
        "owl_name": "secretary",
        "channel": "telegram",
    })

    assert await _wait_for(lambda: len(seen) == 1)
    assert seen[0]["conversation_id"] == "INC_A"
    assert seen[0]["total_usd"] > 0
    assert seen[0]["call_count"] == 1
    assert "message" in seen[0]


async def test_a_free_conversation_reports_nothing(tmp_db: DbPool) -> None:
    """A boundary with no spend is noise, not news — no event, no ping."""
    bus = EventBus()
    tracker = CostTracker(db=tmp_db, event_bus=bus, daily_limit_usd=None)
    seen: list[dict] = []
    bus.subscribe(COST_REPORT_EVENT, lambda payload: seen.append(payload))
    register_conversation_cost_consumer(bus, tracker)

    bus.emit(SessionStore.ROLLOVER_EVENT, {
        "session_key": "owl:secretary:telegram:dm:1",
        "old_conversation_id": "INC_EMPTY",
    })

    await asyncio.sleep(0.2)
    assert seen == []


async def test_a_boundary_that_ended_nothing_reports_nothing(tmp_db: DbPool) -> None:
    """The sweeper legitimately publishes new_conversation_id=None; a missing OLD id
    means nothing finished, so there is nothing to price."""
    bus = EventBus()
    tracker = CostTracker(db=tmp_db, event_bus=bus, daily_limit_usd=None)
    seen: list[dict] = []
    bus.subscribe(COST_REPORT_EVENT, lambda payload: seen.append(payload))
    register_conversation_cost_consumer(bus, tracker)

    bus.emit(SessionStore.ROLLOVER_EVENT, {"session_key": "lane", "old_conversation_id": ""})

    await asyncio.sleep(0.2)
    assert seen == []


async def test_a_failing_aggregate_never_breaks_the_boundary(tmp_db: DbPool) -> None:
    """The conversation starting matters more than its receipt — the same rule
    the rollover summary consumer follows. A pricing failure must be logged and
    swallowed, never raised into the boundary."""
    bus = EventBus()
    seen: list[dict] = []
    bus.subscribe(COST_REPORT_EVENT, lambda payload: seen.append(payload))

    class _Boom:
        async def session_total(self, session_key: str, conversation_id: str) -> object:
            raise RuntimeError("db gone")

    register_conversation_cost_consumer(bus, _Boom())

    bus.emit(SessionStore.ROLLOVER_EVENT, {
        "session_key": "lane", "old_conversation_id": "INC_A",
    })

    await asyncio.sleep(0.2)
    assert seen == []  # no report, and crucially no exception escaped
