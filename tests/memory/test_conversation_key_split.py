"""A conversation's turns are filed under two keys; recall must read both.

BAKIR, 2026-08-16, after the agent failed to resolve a five-minute-old reference:
"our today's short term, long term memory is not working well. That's a big
problem."

WHAT IS ACTUALLY TRUE, measured on the live database rather than assumed. Turns
are WRITTEN under `owner_scope_key(state)` — `identity_key or session_key`. That
key is CONDITIONAL: when identity resolution produces nothing, the turn lands
under the raw lane instead. So one continuous conversation splits between two
buckets according to whether identity happened to resolve on that turn, and a
read of either bucket loses the other.

On Bakir's own Telegram lane: 26 turns under the identity key, 8 under the lane
key, 34 in total. The reader used the identity key, so it saw 76% and lost 8 real
messages — "Compare qwen3.8 27b with qwen 3.6 27b", "Go apply for me in capital
one", "Why?".

WHAT THIS DOES NOT EXPLAIN, and the correction matters. It is NOT why "B retime
it" was met with "What does B refer to?". That turn was in the bucket the reader
DOES read. That failure was the ROUTER, which authored the clarify question with
no conversation attached at all — fixed separately. I first reported this split
as "99% of memory unreadable" by reading a parameter NAME (`session_key=`) and
inferring the value; the caller actually passes `owner_scope_key(state)`. The
measurement corrected the claim.

Reading the union is what makes recall whole, and it keeps working while the
write key stays conditional — no migration, and no dependency on identity
resolution becoming reliable.
"""

from __future__ import annotations

import pytest

from stackowl.memory.sqlite_bridge import SqliteMemoryBridge

IDENTITY = "72055773"
LANE = "owl:secretary:telegram:dm:72055773"


class TestTheKeySetIsWholeAndSafe:
    def test_both_buckets_are_queried(self) -> None:
        refs = SqliteMemoryBridge._conversation_refs(IDENTITY, (LANE,))

        assert set(refs) == {IDENTITY, LANE}

    def test_the_primary_key_stays_first(self) -> None:
        """Order is preserved so the caller's own key leads — it is the one the
        writer used most recently."""
        assert SqliteMemoryBridge._conversation_refs(IDENTITY, (LANE,))[0] == IDENTITY

    def test_a_duplicate_key_is_collapsed(self) -> None:
        """When identity IS the lane (no resolver), both names are the same key
        and the query must not list it twice."""
        assert SqliteMemoryBridge._conversation_refs(LANE, (LANE,)) == (LANE,)

    def test_empty_refs_are_dropped(self) -> None:
        """An empty ref would match every turn that was filed with no key at all —
        someone else's conversation."""
        refs = SqliteMemoryBridge._conversation_refs(IDENTITY, ("", None))  # type: ignore[arg-type]

        assert refs == (IDENTITY,)

    def test_no_extra_refs_behaves_exactly_as_before(self) -> None:
        assert SqliteMemoryBridge._conversation_refs(IDENTITY, ()) == (IDENTITY,)


class TestTheScopeKeysHelper:
    """One source of truth for the key set, so the two readers cannot drift."""

    def _state(self, identity: str, session: str):
        from stackowl.pipeline.state import PipelineState

        return PipelineState(
            trace_id="t", session_key=session, input_text="hi",
            channel="telegram", owl_name="secretary", pipeline_step="",
            identity_key=identity,
        )

    def test_it_returns_both_when_an_identity_exists(self) -> None:
        from stackowl.pipeline.services import conversation_scope_keys

        keys = conversation_scope_keys(self._state(IDENTITY, LANE))

        assert set(keys) == {IDENTITY, LANE}

    def test_it_returns_one_key_when_identity_is_absent(self) -> None:
        """Without a resolver the write key IS the lane, so there is one bucket.
        `identity_key` is a non-optional str on PipelineState, so "absent" is the
        empty string — which is exactly what owner_scope_key's `or` falls through
        on, and exactly the condition that splits a conversation in two."""
        from stackowl.pipeline.services import conversation_scope_keys

        assert conversation_scope_keys(self._state("", LANE)) == (LANE,)

    def test_the_write_key_leads(self) -> None:
        """owner_scope_key is what the writer used, so it is the primary."""
        from stackowl.pipeline.services import conversation_scope_keys, owner_scope_key

        state = self._state(IDENTITY, LANE)

        assert conversation_scope_keys(state)[0] == owner_scope_key(state)


@pytest.mark.asyncio
class TestTheUnionActuallyRecoversTurns:
    async def test_a_turn_in_either_bucket_comes_back(self, tmp_db) -> None:
        """The end-to-end proof: write one turn under each key, read once, get
        both — which is what 'the agent remembers what I just said' means here."""
        bridge = SqliteMemoryBridge(tmp_db)
        await bridge.store("User: under the identity key", IDENTITY)
        await bridge.store("User: under the lane key", LANE)

        turns = await bridge.recent_conversation_turns(
            IDENTITY, limit=10, also_refs=(LANE,),
        )

        contents = " ".join(t.content for t in turns)
        assert "under the identity key" in contents
        assert "under the lane key" in contents, (
            "the lane-bucket turn is still invisible — the split is not closed"
        )

    async def test_without_the_union_one_bucket_is_lost(self, tmp_db) -> None:
        """Pins the defect itself. If this ever stops losing the turn, the test
        above has stopped proving anything."""
        bridge = SqliteMemoryBridge(tmp_db)
        await bridge.store("User: under the identity key", IDENTITY)
        await bridge.store("User: under the lane key", LANE)

        turns = await bridge.recent_conversation_turns(IDENTITY, limit=10)

        contents = " ".join(t.content for t in turns)
        assert "under the lane key" not in contents

    async def test_another_persons_lane_is_never_pulled_in(self, tmp_db) -> None:
        """Widening the read must not widen it to someone else."""
        bridge = SqliteMemoryBridge(tmp_db)
        await bridge.store("User: mine", IDENTITY)
        await bridge.store("User: someone else entirely", "99999999")

        turns = await bridge.recent_conversation_turns(
            IDENTITY, limit=10, also_refs=(LANE,),
        )

        assert "someone else entirely" not in " ".join(t.content for t in turns)
