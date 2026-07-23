"""TaskOutcomeStore.count_approach_ratings_for_owl — the owl-health rating
aggregation query. Unlike list_scored_for_owl, must count a Like/Dislike vote
regardless of whether the critic ever scored the turn (quality_score is NULL
for most turns in practice)."""

from __future__ import annotations

import pytest

from stackowl.db.pool import DbPool
from stackowl.memory.outcome_store import TaskOutcomeStore

_SCHEMA = """
    CREATE TABLE task_outcomes (
        outcome_id INTEGER PRIMARY KEY AUTOINCREMENT, trace_id TEXT NOT NULL,
        session_id TEXT NOT NULL, owl_name TEXT NOT NULL, channel TEXT NOT NULL,
        success INTEGER NOT NULL, latency_ms REAL NOT NULL,
        tool_call_count INTEGER NOT NULL DEFAULT 0, failure_class TEXT,
        quality_score REAL, step_durations TEXT NOT NULL DEFAULT '{}',
        input_text TEXT NOT NULL DEFAULT '', response_text TEXT NOT NULL DEFAULT '',
        captured_at REAL NOT NULL, scored_at REAL, owner_id TEXT NOT NULL DEFAULT 'principal-default',
        tool_sequence TEXT NOT NULL DEFAULT '[]', dna_snapshot TEXT NOT NULL DEFAULT '{}',
        overclaim_blocked INTEGER NOT NULL DEFAULT 0, recovered_via_tool TEXT,
        failed_capability TEXT, approach_rating TEXT,
        retry_lineage_id TEXT, retry_event_count INTEGER NOT NULL DEFAULT 0,
        UNIQUE(trace_id)
    )
"""


async def _make_store(tmp_path) -> tuple[DbPool, TaskOutcomeStore]:
    db = DbPool(tmp_path / "test.db")
    await db.open()
    await db.execute(_SCHEMA)
    return db, TaskOutcomeStore(db)


@pytest.mark.asyncio
async def test_counts_only_rated_outcomes_for_the_named_owl(tmp_path) -> None:
    db, store = await _make_store(tmp_path)
    await store.record(
        trace_id="t1", session_id="s", owl_name="scout", channel="telegram",
        success=True, latency_ms=1.0, tool_call_count=0, failure_class=None,
        step_durations={}, input_text="", response_text="",
    )
    await store.record(
        trace_id="t2", session_id="s", owl_name="scout", channel="telegram",
        success=True, latency_ms=1.0, tool_call_count=0, failure_class=None,
        step_durations={}, input_text="", response_text="",
    )
    await store.record(
        trace_id="t3", session_id="s", owl_name="scout", channel="telegram",
        success=True, latency_ms=1.0, tool_call_count=0, failure_class=None,
        step_durations={}, input_text="", response_text="",
    )
    # A different owl's dislike must not count toward "scout".
    await store.record(
        trace_id="t4", session_id="s", owl_name="sage", channel="telegram",
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
async def test_uncritic_scored_votes_still_count(tmp_path) -> None:
    """quality_score stays NULL (critic never ran) — the vote must still count,
    unlike list_scored_for_owl which requires quality_score IS NOT NULL."""
    db, store = await _make_store(tmp_path)
    await store.record(
        trace_id="t1", session_id="s", owl_name="scout", channel="telegram",
        success=True, latency_ms=1.0, tool_call_count=0, failure_class=None,
        step_durations={}, input_text="", response_text="",
    )
    await store.set_approach_rating(trace_id="t1", rating="negative")

    rows = await db.fetch_all("SELECT quality_score FROM task_outcomes WHERE trace_id = ?", ("t1",))
    assert rows[0]["quality_score"] is None

    positive, negative = await store.count_approach_ratings_for_owl("scout")
    assert (positive, negative) == (0, 1)


@pytest.mark.asyncio
async def test_no_rated_outcomes_returns_zero_zero(tmp_path) -> None:
    _, store = await _make_store(tmp_path)
    assert await store.count_approach_ratings_for_owl("ghost") == (0, 0)
