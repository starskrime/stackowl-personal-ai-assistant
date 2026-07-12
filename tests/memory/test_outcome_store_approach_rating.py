import pytest

from stackowl.db.pool import DbPool
from stackowl.memory.outcome_store import TaskOutcomeStore


@pytest.mark.asyncio
async def test_set_approach_rating_updates_existing_row(tmp_path):
    db = DbPool(tmp_path / "test.db")
    await db.open()
    await db.execute("""
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
            failed_capability TEXT, approach_rating TEXT, UNIQUE(trace_id)
        )
    """)
    store = TaskOutcomeStore(db)
    await store.record(
        trace_id="trace-1", session_id="s1", owl_name="secretary", channel="telegram",
        success=True, latency_ms=100.0, tool_call_count=1, failure_class=None,
        step_durations={}, input_text="hi", response_text="hello",
    )

    updated = await store.set_approach_rating(trace_id="trace-1", rating="positive")
    assert updated is True

    rows = await db.fetch_all("SELECT approach_rating FROM task_outcomes WHERE trace_id = ?", ("trace-1",))
    assert rows[0]["approach_rating"] == "positive"


@pytest.mark.asyncio
async def test_set_approach_rating_missing_row_returns_false(tmp_path):
    db = DbPool(tmp_path / "test.db")
    await db.open()
    await db.execute("""
        CREATE TABLE task_outcomes (
            outcome_id INTEGER PRIMARY KEY AUTOINCREMENT, trace_id TEXT NOT NULL,
            session_id TEXT NOT NULL, owl_name TEXT NOT NULL, channel TEXT NOT NULL,
            success INTEGER NOT NULL, latency_ms REAL NOT NULL,
            tool_call_count INTEGER NOT NULL DEFAULT 0, failure_class TEXT,
            quality_score REAL, step_durations TEXT NOT NULL DEFAULT '{}',
            input_text TEXT NOT NULL DEFAULT '', response_text TEXT NOT NULL DEFAULT '',
            captured_at REAL NOT NULL, scored_at REAL, owner_id TEXT NOT NULL DEFAULT 'principal-default',
            approach_rating TEXT, UNIQUE(trace_id)
        )
    """)
    store = TaskOutcomeStore(db)

    updated = await store.set_approach_rating(trace_id="nonexistent", rating="negative")
    assert updated is False
