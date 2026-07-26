"""D01.7 slice 3a.2 — the incarnation rides the turn alongside the lane.

The lane (``session_key``) says WHICH conversation. The incarnation
(``session_id``) says WHICH RUN of it. Both have to reach the cost row, or
`D01.1`'s byte-identical-prompt invariant has nothing correct to group by:
grouping by lane spans every rollover the lane has ever had, which is exactly the
mistake the `D01.6` baseline recorded (10 distinct prompts on one lane).

These tests pin the carrier, not the policy — resolution itself is already
covered by tests/sessions/test_session_store.py.
"""

from __future__ import annotations

import pytest

from stackowl.infra.trace import TraceContext


def test_the_lane_and_the_incarnation_are_carried_separately() -> None:
    token = TraceContext.start("owl:Brain:telegram:dm:123", session_id="20260725_040000_abcd1234")
    try:
        ctx = TraceContext.get()
        assert ctx["session_key"] == "owl:Brain:telegram:dm:123"
        assert ctx["session_id"] == "20260725_040000_abcd1234"
    finally:
        TraceContext.reset(token)


def test_a_turn_without_a_resolved_incarnation_is_honest_about_it() -> None:
    """Background work that never went through ingress has a lane but no
    incarnation. That must read as None, never as a fabricated id."""
    token = TraceContext.start("goal-owl_lifecycle-Brain")
    try:
        assert TraceContext.get()["session_id"] is None
    finally:
        TraceContext.reset(token)


def test_the_incarnation_does_not_leak_out_of_the_turn() -> None:
    """contextvars are reset by token; a leaked incarnation would attribute the
    NEXT turn's cost to the previous conversation."""
    token = TraceContext.start("owl:Brain:telegram:dm:123", session_id="20260725_040000_abcd1234")
    TraceContext.reset(token)
    assert TraceContext.get()["session_id"] is None


def test_the_incarnation_is_log_safe_and_reaches_every_record() -> None:
    """It is in get(), so JsonlFormatter stamps it on every line of the turn —
    which is what makes 'show me everything that happened in that conversation'
    answerable from the log alone."""
    token = TraceContext.start("owl:Brain:cli:dm:1", session_id="20260725_040000_abcd1234")
    try:
        assert "session_id" in TraceContext.get()
    finally:
        TraceContext.reset(token)


@pytest.mark.asyncio
async def test_the_cost_row_records_both_ids() -> None:
    """The whole point of the threading: a cost row that can be grouped by
    conversation RUN, not merely by lane."""
    import tempfile
    from pathlib import Path

    from stackowl.db.migrations.runner import MigrationRunner
    from stackowl.db.pool import DbPool
    from stackowl.events.bus import EventBus
    from stackowl.providers.cost_tracker import CostTracker

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "t.db"
        MigrationRunner(path).run()
        db = DbPool(db_path=path)
        await db.open()
        try:
            tracker = CostTracker(db=db, event_bus=EventBus())
            await tracker.record(
                provider_name="p", model="m", input_tokens=10, output_tokens=2,
                duration_ms=1.0, trace_id="t1", is_local=False,
                session_key="owl:Brain:telegram:dm:123",
                session_id="20260725_040000_abcd1234",
            )
            rows = await db.fetch_all(
                "SELECT session_key, session_id FROM cost_records WHERE trace_id = ?", ("t1",))
            assert rows, "cost row was not written"
            assert rows[0]["session_key"] == "owl:Brain:telegram:dm:123"
            assert rows[0]["session_id"] == "20260725_040000_abcd1234"
        finally:
            await db.close()
