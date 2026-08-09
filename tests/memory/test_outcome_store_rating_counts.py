"""TaskOutcomeStore.count_approach_ratings_for_owl — the owl-health rating
aggregation query. Unlike list_scored_for_owl, must count a Like/Dislike vote
regardless of whether the critic ever scored the turn (quality_score is NULL
for most turns in practice)."""

from __future__ import annotations

import pytest

from stackowl.memory.outcome_store import TaskOutcomeStore

# The hand-rolled task_outcomes DDL and the DbPool factory that stood here are
# gone. Two reasons, and the second is why these tests were RED:
#
#   * The copied schema was a second definition of a real table, free to drift
#     from the migrations that actually build it.
#   * ``_make_store`` called ``db.open()`` and never closed the pool, so when the
#     test's event loop went away aiosqlite's worker thread raised "Event loop is
#     closed" and pytest surfaced it as an unhandled thread exception.
#
# ``tmp_db`` (tests/conftest.py) runs the real migrations and closes the pool in
# a finally, which fixes both at once.


@pytest.mark.asyncio
async def test_counts_only_rated_outcomes_for_the_named_owl(tmp_db) -> None:
    store = TaskOutcomeStore(tmp_db)
    await store.record(
        trace_id="t1", session_key="s", owl_name="scout", channel="telegram",
        success=True, latency_ms=1.0, tool_call_count=0, failure_class=None,
        step_durations={}, input_text="", response_text="",
    )
    await store.record(
        trace_id="t2", session_key="s", owl_name="scout", channel="telegram",
        success=True, latency_ms=1.0, tool_call_count=0, failure_class=None,
        step_durations={}, input_text="", response_text="",
    )
    await store.record(
        trace_id="t3", session_key="s", owl_name="scout", channel="telegram",
        success=True, latency_ms=1.0, tool_call_count=0, failure_class=None,
        step_durations={}, input_text="", response_text="",
    )
    # A different owl's dislike must not count toward "scout".
    await store.record(
        trace_id="t4", session_key="s", owl_name="sage", channel="telegram",
        success=True, latency_ms=1.0, tool_call_count=0, failure_class=None,
        step_durations={}, input_text="", response_text="",
    )
    await store.set_approach_rating(trace_id="t1", rating="positive")
    await store.set_approach_rating(trace_id="t2", rating="negative")
    # t3 stays unrated — must not be counted as either.
    await store.set_approach_rating(trace_id="t4", rating="negative")

    positive, negative = await store.count_approach_ratings_for_owl("scout")

    assert (positive, negative) == (1, 1)


@pytest.mark.asyncio
async def test_uncritic_scored_votes_still_count(tmp_db) -> None:
    """quality_score stays NULL (critic never ran) — the vote must still count,
    unlike list_scored_for_owl which requires quality_score IS NOT NULL."""
    store = TaskOutcomeStore(tmp_db)
    await store.record(
        trace_id="t1", session_key="s", owl_name="scout", channel="telegram",
        success=True, latency_ms=1.0, tool_call_count=0, failure_class=None,
        step_durations={}, input_text="", response_text="",
    )
    await store.set_approach_rating(trace_id="t1", rating="negative")

    rows = await tmp_db.fetch_all("SELECT quality_score FROM task_outcomes WHERE trace_id = ?", ("t1",))
    assert rows[0]["quality_score"] is None

    positive, negative = await store.count_approach_ratings_for_owl("scout")
    assert (positive, negative) == (0, 1)


@pytest.mark.asyncio
async def test_no_rated_outcomes_returns_zero_zero(tmp_db) -> None:
    store = TaskOutcomeStore(tmp_db)
    assert await store.count_approach_ratings_for_owl("ghost") == (0, 0)
