"""TaskOutcomeStore.set_approach_rating — the Like/Dislike button write.

Rewritten onto the shared ``tmp_db`` fixture. Both tests previously built their
own DbPool and their own copy of the task_outcomes DDL, which was wrong twice
over:

  * The pool was opened and never closed, so when the test's event loop went
    away aiosqlite's worker thread raised "Event loop is closed" and pytest
    surfaced it as an unhandled thread exception — these tests were RED.
  * The copied schema was a second definition of a real table, free to drift
    from the migrations that build it. The two copies had ALREADY diverged: the
    first test's version carried tool_sequence, dna_snapshot, overclaim_blocked,
    recovered_via_tool, failed_capability and the retry columns; the second's
    did not. Neither was necessarily what the migrations produce.

``tmp_db`` runs the real migrations and closes the pool in a finally.
"""

from __future__ import annotations

import pytest

from stackowl.memory.outcome_store import TaskOutcomeStore

pytestmark = pytest.mark.asyncio


async def test_set_approach_rating_updates_existing_row(tmp_db) -> None:
    store = TaskOutcomeStore(tmp_db)
    await store.record(
        trace_id="trace-1", session_key="s1", owl_name="secretary", channel="telegram",
        success=True, latency_ms=100.0, tool_call_count=1, failure_class=None,
        step_durations={}, input_text="hi", response_text="hello",
    )

    updated = await store.set_approach_rating(trace_id="trace-1", rating="positive")

    assert updated is True
    rows = await tmp_db.fetch_all(
        "SELECT approach_rating FROM task_outcomes WHERE trace_id = ?", ("trace-1",)
    )
    assert rows[0]["approach_rating"] == "positive"


async def test_set_approach_rating_missing_row_returns_false(tmp_db) -> None:
    """A vote on a turn we never recorded reports failure rather than silently
    writing nothing — the button needs to know it did not land."""
    store = TaskOutcomeStore(tmp_db)

    updated = await store.set_approach_rating(trace_id="nonexistent", rating="negative")

    assert updated is False
