"""`messages.model` was a second home for a fact `cost_records` already owns.

MEASURED 2026-09-04, working D11.1. The map's parity claim includes "model
config recorded per session". We are AHEAD of that — and I read it backwards
first, which is why the numbers are here.

`messages.model` exists. It is populated in **0 of 3,841 rows**. My first
conclusion was that we were BEHIND: a column nobody fills. That was the wrong
table. `cost_records` holds **130,420 rows with `model` populated in 100% of
them**, alongside provider, input/output tokens, cost, TTFT, prompt hash — and
`trace_id`, `conversation_id`, `session_key` and `owl_name` to join on. The model
that served every call is recorded richly. It is recorded ONE TABLE OVER, which
is the same mistake this programme already paid for when `committed_facts` was
empty and 361 real memories were sitting in `staged_facts`.

So `messages.model` is not a gap. It is the third shape in `CLAUDE.md`: two
copies of one rule. And the copy that lost is dead on every axis measured:

    rows populated   0 of 3,841
    readers          0 — every SELECT over `messages` names id, role, content,
                     created_at, and never model
    writers          1 INSERT, handed None by its only caller, which does not
                     pass `model=` at all
    tests            0 passing `model=`

Nor could the caller supply it: `PipelineState` carries no field naming the model
that answered — only `model_window`. The column could not be filled from the path
that writes it.

AND IT WOULD BE LOSSY IF IT WERE. 130,420 cost records against 3,841 messages is
~34 model calls per recorded message. A single `model` string on a message cannot
represent that without a rule for which of the 34 counts, and a summary that can
disagree with the ledger is worse than no summary.

Migration 0110 is the precedent, and its reasoning is the same sentence: "Two
writers to one fact is how fields drift." That drop removed a column populated in
169 of 170 rows. This one removes a column populated in none.
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_messages_no_longer_carries_a_model_column(tmp_db) -> None:
    cols = {r["name"] for r in await tmp_db.fetch_all("PRAGMA table_info(messages)")}

    assert "model" not in cols, (
        "a column with no reader, no supplied writer and no rows is the dead seat "
        "the retirement rule forbids"
    )
    # The columns that carry the transcript must survive the drop intact —
    # SQLite's DROP COLUMN rewrites the table, so this is not a formality.
    assert {"id", "conversation_id", "role", "content", "created_at",
            "trace_id", "owner_id"} <= cols


@pytest.mark.asyncio
async def test_a_turn_still_records_both_sides(tmp_db) -> None:
    """The control. Dropping the column must not disturb what the table is FOR."""
    from stackowl.memory.transcript_store import TranscriptStore

    written = await TranscriptStore(tmp_db).record_turn(
        session_key="s", conversation_id="c", owl_name="Daria",
        user_text="hello", assistant_text="hi there", trace_id="t",
    )

    assert written == 2
    rows = await tmp_db.fetch_all(
        "SELECT role, content FROM messages ORDER BY role"
    )
    assert [(r["role"], r["content"]) for r in rows] == [
        ("assistant", "hi there"), ("user", "hello"),
    ]


def test_record_turn_no_longer_advertises_a_model_it_never_received() -> None:
    """The signature is the promise. Leaving the parameter would keep inviting a
    caller to fill a column that is gone."""
    import inspect

    from stackowl.memory.transcript_store import TranscriptStore

    params = inspect.signature(TranscriptStore.record_turn).parameters
    assert "model" not in params
    assert {"session_key", "conversation_id", "user_text", "assistant_text"} <= set(params)


@pytest.mark.asyncio
async def test_the_fact_still_has_exactly_one_home(tmp_db) -> None:
    """`cost_records` is where the model lives, and it is joinable back to a turn.

    If this ever fails, the drop above removed the only copy rather than the
    duplicate — the one way this change could be wrong.
    """
    cols = {r["name"] for r in await tmp_db.fetch_all("PRAGMA table_info(cost_records)")}

    assert "model" in cols
    assert {"trace_id", "conversation_id", "session_key"} <= cols, (
        "the surviving copy must be joinable to the transcript it describes"
    )
