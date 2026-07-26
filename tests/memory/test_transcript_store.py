"""D01.7 — the transcript is actually written, against a real database.

Real DbPool, real migrations, temp HOME. These tests are the proof that six
readers (owl DNA evolution, fact extraction, session search, transcripts,
session access, cron owner lookup) stop reading an empty table — so they assert
through the SAME join those readers use, not through the writer's own API.
"""

from __future__ import annotations

import datetime

import pytest

from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.memory.transcript_store import TranscriptStore

UTC = datetime.UTC
LANE = "owl:Brain:telegram:dm:123"
RUN = "20260725_040000_abcd1234"


@pytest.fixture
async def store(tmp_path, monkeypatch):
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path))
    path = tmp_path / "t.db"
    MigrationRunner(path).run()
    db = DbPool(db_path=path)
    await db.open()
    yield TranscriptStore(db), db
    await db.close()


@pytest.mark.asyncio
async def test_a_clean_turn_records_both_sides(store) -> None:
    ts, db = store
    written = await ts.record_turn(
        session_key=LANE, session_id=RUN, owl_name="Brain",
        user_text="what time is it?", assistant_text="just gone four.",
        trace_id="t1",
    )
    assert written == 2
    rows = await db.fetch_all(
        "SELECT role, content FROM messages ORDER BY role DESC")
    assert [r["role"] for r in rows] == ["user", "assistant"]


@pytest.mark.asyncio
async def test_the_six_readers_join_finds_it(store) -> None:
    """The exact shape owls/evolution.py and extraction_handler.py use."""
    ts, db = store
    await ts.record_turn(session_key=LANE, session_id=RUN, owl_name="Brain",
                         user_text="hello", assistant_text="hi")
    rows = await db.fetch_all(
        "SELECT m.content FROM messages m "
        "JOIN conversations c ON c.id = m.conversation_id WHERE c.owl_name = ?",
        ("Brain",))
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_a_floored_turn_records_the_user_only(store) -> None:
    """A transcript containing 'I did it' for a turn that did not is worse than
    a gap — this mirrors what persist_turn already does for staged facts."""
    ts, db = store
    written = await ts.record_turn(
        session_key=LANE, session_id=RUN, owl_name="Brain",
        user_text="deploy it", assistant_text=None)
    assert written == 1
    rows = await db.fetch_all("SELECT role FROM messages")
    assert [r["role"] for r in rows] == ["user"]


@pytest.mark.asyncio
async def test_work_without_an_incarnation_is_not_a_conversation(store) -> None:
    """Scheduler handlers and parliament rounds have a lane but never passed
    through ingress. They must not invent a conversation."""
    ts, db = store
    assert await ts.record_turn(session_key="goal-x", session_id="", owl_name="Brain",
                                user_text="tick", assistant_text="tock") == 0
    assert await db.fetch_all("SELECT id FROM conversations") == []


@pytest.mark.asyncio
async def test_turns_accumulate_into_one_conversation(store) -> None:
    ts, db = store
    for i in range(3):
        await ts.record_turn(session_key=LANE, session_id=RUN, owl_name="Brain",
                             user_text=f"q{i}", assistant_text=f"a{i}")
    convs = await db.fetch_all("SELECT id, message_count FROM conversations")
    assert len(convs) == 1, "one incarnation is one conversation"
    assert convs[0]["message_count"] == 6
    assert len(await db.fetch_all("SELECT id FROM messages")) == 6


@pytest.mark.asyncio
async def test_i6_a_rollover_never_touches_the_old_transcript(store) -> None:
    """Invariant I6, now testable: a new incarnation starts a NEW conversation
    and the previous one survives intact."""
    ts, db = store
    await ts.record_turn(session_key=LANE, session_id=RUN, owl_name="Brain",
                         user_text="yesterday", assistant_text="ok")
    await ts.record_turn(session_key=LANE, session_id="20260726_040000_beef0000",
                         owl_name="Brain", user_text="today", assistant_text="ok")
    convs = await db.fetch_all("SELECT id FROM conversations ORDER BY started_at")
    assert len(convs) == 2, "a rollover starts a new transcript"
    old = await db.fetch_all(
        "SELECT content FROM messages WHERE conversation_id = ? AND role = 'user'", (RUN,))
    assert [r["content"] for r in old] == ["yesterday"], "the old transcript is intact"


@pytest.mark.asyncio
async def test_an_empty_user_utterance_records_nothing(store) -> None:
    ts, db = store
    assert await ts.record_turn(session_key=LANE, session_id=RUN, owl_name="Brain",
                                user_text="", assistant_text="hi") == 0
    assert await db.fetch_all("SELECT id FROM messages") == []
