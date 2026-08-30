"""D05.2's session-boundary half — the tools array must not move mid-conversation.

D05.2 replaced a `request_text` ranker with MEASURED per-owl usage and recorded
that the presented array was thereby "stable for the life of a session by
construction". It was stable against the QUESTION, not against TIME: the scores
are read live and they move as the owl uses tools.

MEASURED 2026-08-30 by running the item's own closing query, denominator first:
12 (conversation, owl) pairs had 2+ turns — real opportunity — and the audit fired
TWICE in the operator's live Telegram lane, once removing 20+ tools and once
adding 3 back. The item predicted silence.
"""

from __future__ import annotations

from stackowl.tools._infra.usage_snapshot import UsageScoreSnapshot


async def test_the_scores_are_read_ONCE_per_conversation() -> None:
    """The whole point: running a tool must not re-order the array mid-conversation."""
    reads = 0

    async def _read() -> dict[str, float]:
        nonlocal reads
        reads += 1
        # the store MOVES between turns — that is the defect being frozen out
        return {"web_fetch": float(reads), "shell": 1.0}

    snap = UsageScoreSnapshot()
    first = await snap.get("conv-1", "secretary", _read)
    second = await snap.get("conv-1", "secretary", _read)
    third = await snap.get("conv-1", "secretary", _read)

    assert reads == 1, "the scores were re-read mid-conversation"
    assert first == second == third


async def test_a_DIFFERENT_conversation_reads_fresh() -> None:
    """Freezing is per incarnation. A new conversation is exactly where D01.1
    permits the prompt to change, so it must pick up newer measurements."""
    reads = 0

    async def _read() -> dict[str, float]:
        nonlocal reads
        reads += 1
        return {"web_fetch": float(reads)}

    snap = UsageScoreSnapshot()
    await snap.get("conv-1", "secretary", _read)
    second = await snap.get("conv-2", "secretary", _read)

    assert reads == 2
    assert second == {"web_fetch": 2.0}


async def test_the_same_conversation_with_a_DIFFERENT_OWL_reads_fresh() -> None:
    """One lane can run several owls (D01.6), and they have different usage."""
    seen: list[str] = []

    async def _make(owl: str):
        async def _read() -> dict[str, float]:
            seen.append(owl)
            return {owl: 1.0}
        return _read

    snap = UsageScoreSnapshot()
    await snap.get("conv-1", "secretary", await _make("secretary"))
    await snap.get("conv-1", "verifier", await _make("verifier"))

    assert seen == ["secretary", "verifier"]


async def test_an_EMPTY_read_is_not_frozen() -> None:
    """A transient store failure must not pin a whole conversation to cold-start
    order. {} means "order by name" for ONE turn, not for the session."""
    calls = 0

    async def _read() -> dict[str, float]:
        nonlocal calls
        calls += 1
        return {} if calls == 1 else {"shell": 2.0}

    snap = UsageScoreSnapshot()
    assert await snap.get("conv-1", "secretary", _read) == {}
    assert await snap.get("conv-1", "secretary", _read) == {"shell": 2.0}
    assert calls == 2


async def test_the_map_is_BOUNDED_and_evicts_the_oldest() -> None:
    """An unbounded per-conversation map leaks for the life of the process."""
    async def _read() -> dict[str, float]:
        return {"shell": 1.0}

    snap = UsageScoreSnapshot(max_tracked=3)
    for i in range(5):
        await snap.get(f"conv-{i}", "secretary", _read)
    assert snap.tracked() == 3


async def test_eviction_keeps_the_MOST_RECENTLY_USED() -> None:
    """Evicting the conversation currently running would restore the very bug
    this removes — it would re-read scores mid-session for the live lane."""
    async def _read() -> dict[str, float]:
        return {"shell": 1.0}

    snap = UsageScoreSnapshot(max_tracked=2)
    await snap.get("old", "secretary", _read)
    await snap.get("mid", "secretary", _read)
    await snap.get("old", "secretary", _read)   # touch: "old" is now the live one
    await snap.get("new", "secretary", _read)

    reads = 0

    async def _counting() -> dict[str, float]:
        nonlocal reads
        reads += 1
        return {"shell": 1.0}

    await snap.get("old", "secretary", _counting)
    assert reads == 0, "the most-recently-used conversation was evicted"


# --------------------------------------------------------------------------- #
# WIRING. The tests above prove the snapshot is CORRECT. They cannot prove it is
# REACHED — and a correct-but-unreached mechanism has shipped in this programme
# five times now. This drives the real `_tool_usage_scores`, so unwiring the
# snapshot there fails a test even though every test above still passes.
# --------------------------------------------------------------------------- #


class _FakeState:
    def __init__(self, conversation_id: str | None) -> None:
        self.conversation_id = conversation_id
        self.owl_name = "secretary"
        self.trace_id = "t1"


async def _drive(monkeypatch, conversation_id: str | None, calls: list[int]) -> dict:
    """Call the REAL _tool_usage_scores with the store read counted."""
    import stackowl.tools._infra.tool_usage as tu
    from stackowl.pipeline.steps import execute as ex

    monkeypatch.setattr(ex, "get_services", lambda: type("S", (), {"db_pool": object()})())
    monkeypatch.setattr("stackowl.memory.outcome_store.TaskOutcomeStore", lambda db: db)

    async def _score(_store, _owl):
        calls.append(1)
        return {"web_fetch": float(len(calls))}

    monkeypatch.setattr(tu, "score_tools_for_owl", _score)
    return await ex._tool_usage_scores(_FakeState(conversation_id))


async def test_the_REAL_read_path_freezes_within_one_conversation(monkeypatch) -> None:
    """The mutation target: unwire the snapshot in execute.py and this dies."""
    import stackowl.tools._infra.usage_snapshot as us
    monkeypatch.setattr(us, "_SHARED", us.UsageScoreSnapshot())

    calls: list[int] = []
    first = await _drive(monkeypatch, "conv-live", calls)
    second = await _drive(monkeypatch, "conv-live", calls)

    assert len(calls) == 1, "the real path re-read the scores mid-conversation"
    assert first == second


async def test_the_REAL_read_path_still_works_with_NO_conversation_id(monkeypatch) -> None:
    """A one-shot run has no second turn to be consistent with. It must still get
    its ordering — a missing id may never cost a turn its tool ranking, which is
    the failure mode of "just skip the read when you cannot cache it"."""
    import stackowl.tools._infra.usage_snapshot as us
    monkeypatch.setattr(us, "_SHARED", us.UsageScoreSnapshot())

    calls: list[int] = []
    scores = await _drive(monkeypatch, None, calls)

    assert scores == {"web_fetch": 1.0}
    assert len(calls) == 1
