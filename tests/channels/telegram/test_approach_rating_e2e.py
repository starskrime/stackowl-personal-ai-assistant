"""Full loop: qualifying answer -> keyboard attached -> sent -> tapped -> recorded + edited.

End-to-end regression test for the approach-rating feature (Task 7 of the
approach-rating-buttons plan). Real DB (via the actual MigrationRunner, not a
hand-copied CREATE TABLE — a DDL snapshot silently drifts from the real schema;
see test_migration_0083.py for the same fixture pattern), real TaskOutcomeStore,
real ApproachRatingTracker + ApproachRatingCallbackHandler. Only the Telegram
adapter's edit_message is mocked.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from stackowl.channels.telegram.approach_rating import (
    ApproachRatingCallbackHandler,
    ApproachRatingTracker,
)
from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.memory.outcome_store import TaskOutcomeStore


@pytest.mark.asyncio
async def test_full_approach_rating_loop(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    MigrationRunner(db_path=db_path).run()

    db = DbPool(db_path)
    await db.open()
    try:
        store = TaskOutcomeStore(db)
        await store.record(
            trace_id="trace-e2e", session_id="s1", owl_name="secretary", channel="telegram",
            success=True, latency_ms=50.0, tool_call_count=0, failure_class=None,
            step_durations={}, input_text="prepare me for the interview",
            response_text="here's your plan...",
        )

        tracker = ApproachRatingTracker(db)
        await tracker.record_pending(trace_id="trace-e2e", text="here's your plan...")
        await tracker.backfill_message(trace_id="trace-e2e", chat_id=42, message_id=100)

        adapter = MagicMock()
        adapter.edit_message = AsyncMock()

        handler = ApproachRatingCallbackHandler(tracker=tracker, outcome_store=store, adapter=adapter)

        await handler.handle("cb-1", "apr:trace-e2e:positive")

        rows = await db.fetch_all(
            "SELECT approach_rating FROM task_outcomes WHERE trace_id = ?", ("trace-e2e",)
        )
        assert rows[0]["approach_rating"] == "positive"
        adapter.edit_message.assert_awaited_once_with(
            42, 100, "here's your plan...\n\n\U0001F44D Liked", reply_markup=None
        )
        assert await tracker.get_message(trace_id="trace-e2e") is None  # cleared after vote
    finally:
        await db.close()
