"""The recorded reply length must be the TRUE one, not the truncated one.

`response_text` is capped at 8,000 characters on write, so
`length(response_text)` is a floor rather than a measurement — counted
2026-08-07, 450 of 3,674 successful replies in 30 days sat at exactly 8,000.

That censoring made a real question unanswerable. After the system prompt was
tightened to a 2048-token reply budget, "did replies actually get shorter?"
could not be answered from this table: every answer over 8k reads identically
before and after, and those are exactly the answers the budget targets.

Same shape as the phantom cloud pricing and the zero-second timeout — a column
that looks like a measurement and is not.
"""

from __future__ import annotations

import pytest

from stackowl.memory.outcome_store import TaskOutcomeStore

_TRUNCATION = 8000


async def _record(store, db, trace: str, reply: str) -> dict:  # noqa: ANN001
    await store.record(
        trace_id=trace, session_key="s", owl_name="o", channel="cli",
        success=True, latency_ms=1.0, tool_call_count=0,
        failure_class=None, step_durations={},
        input_text="q", response_text=reply,
    )
    rows = await db.fetch_all(
        "SELECT response_chars, length(response_text) AS stored "
        "FROM task_outcomes WHERE trace_id = ?", (trace,),
    )
    return dict(rows[0])


@pytest.mark.asyncio
async def test_a_reply_LONGER_than_the_cap_records_its_true_length(tmp_db):
    """THE POINT. This is the case the old metric could not see at all."""
    store = TaskOutcomeStore(tmp_db)
    reply = "x" * 25_000

    row = await _record(store, tmp_db, "t-long", reply)

    assert row["response_chars"] == 25_000, "the true length must survive"
    assert row["stored"] == _TRUNCATION, (
        "the stored TEXT is still capped — this change measures the reply, it "
        "does not start keeping more of it"
    )


@pytest.mark.asyncio
async def test_a_short_reply_records_the_same_number_both_ways(tmp_db):
    """Below the cap the two agree, so nothing about existing analysis changes
    for the replies that were already measurable."""
    store = TaskOutcomeStore(tmp_db)

    row = await _record(store, tmp_db, "t-short", "y" * 120)

    assert row["response_chars"] == 120
    assert row["stored"] == 120


@pytest.mark.asyncio
async def test_a_reply_exactly_at_the_cap_is_not_ambiguous(tmp_db):
    """8,000 used to mean "8,000 or more". It must now mean 8,000."""
    store = TaskOutcomeStore(tmp_db)

    row = await _record(store, tmp_db, "t-exact", "z" * _TRUNCATION)

    assert row["response_chars"] == _TRUNCATION
    assert row["stored"] == _TRUNCATION


@pytest.mark.asyncio
async def test_an_empty_reply_records_zero_not_null(tmp_db):
    """NULL is reserved for "never recorded" (pre-migration rows). A genuinely
    empty reply is a measurement of zero, and the two must stay
    distinguishable — a floored turn is not the same as an unmeasured one."""
    store = TaskOutcomeStore(tmp_db)

    row = await _record(store, tmp_db, "t-empty", "")

    assert row["response_chars"] == 0
    assert row["response_chars"] is not None
