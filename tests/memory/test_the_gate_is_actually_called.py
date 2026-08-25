"""The gate has callers — because a gate nothing calls is this codebase's defect.

Bakir asked for something "lightweight and powerful to avoid remembering the same
thing again". The gate shipped first with 23 tests and NO CALLERS, which is
exactly the shape this session found SIX times: FactReinforcer (the deduplicator
with no callers), add_relation (the RELATED_TO graph edges), is_machine_lane,
reinforcement_count (a column whose only writer had no callers), CuratedMemory's
`target` (returned but never spoken), and the embedding_registry the bridge held
and never used. The recurring defect here is not missing design — it is design
that never got a caller. These tests exist so the gate does not become the
seventh.

WHAT THEY ASSERT is the EFFECT, not the call: a second write of the same fact
must not produce a second row, and must leave the first row's TEXT untouched
while its reinforcement_count rises. That is Bakir's decision — "keep stored,
bump the counter" — and asserting the effect rather than the call is what stops
this passing while the gate silently stops running.
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.memory.sqlite_bridge import SqliteMemoryBridge

LANE = "72055773"


@pytest.mark.asyncio
async def test_the_same_fact_twice_writes_ONE_row(tmp_db: Any) -> None:
    """The whole point. staged_facts reached 66% exact duplicates without this."""
    bridge = SqliteMemoryBridge(tmp_db)

    await bridge.store("Bakir prefers root-cause fixes.", LANE)
    await bridge.store("Bakir prefers root-cause fixes.", LANE)

    rows = await tmp_db.fetch_all("SELECT fact_id, content FROM staged_facts")
    assert len(rows) == 1, "the second write must reinforce, not insert"


@pytest.mark.asyncio
async def test_a_reworded_duplicate_is_also_caught(tmp_db: Any) -> None:
    """Rung 1 folds case and punctuation — 66% of the real duplicates were this."""
    bridge = SqliteMemoryBridge(tmp_db)

    await bridge.store("Bakir prefers root-cause fixes.", LANE)
    await bridge.store("bakir prefers root cause fixes", LANE)

    rows = await tmp_db.fetch_all("SELECT content FROM staged_facts")
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_the_stored_text_is_NOT_rewritten(tmp_db: Any) -> None:
    """"Keep stored, bump the counter." Supersede was considered and rejected:
    a fact must not change wording under a reader who already learned it."""
    bridge = SqliteMemoryBridge(tmp_db)

    await bridge.store("Bakir prefers root-cause fixes.", LANE)
    await bridge.store("bakir prefers root cause fixes", LANE)

    row = (await tmp_db.fetch_all("SELECT content FROM staged_facts"))[0]
    assert row["content"] == "Bakir prefers root-cause fixes.", (
        "the ORIGINAL wording survives; the newer phrasing does not overwrite it"
    )


@pytest.mark.asyncio
async def test_the_counter_actually_rises(tmp_db: Any) -> None:
    """reinforcement_count read 0 on all 5,212 rows of the old table — the column
    existed and nothing ever incremented it. recall_ranker consumes it with a
    saturating 1 + k*ln(1+n) boost, so the signal has a reader waiting."""
    bridge = SqliteMemoryBridge(tmp_db)

    await bridge.store("Bakir prefers root-cause fixes.", LANE)
    await bridge.store("Bakir prefers root-cause fixes.", LANE)
    await bridge.store("BAKIR PREFERS ROOT-CAUSE FIXES", LANE)

    row = (await tmp_db.fetch_all(
        "SELECT reinforcement_count FROM staged_facts"
    ))[0]
    assert row["reinforcement_count"] == 2, (
        "two re-statements after the original must count as two reinforcements"
    )


@pytest.mark.asyncio
async def test_a_genuinely_different_fact_is_still_stored(tmp_db: Any) -> None:
    """The gate must not turn memory off. This is the failure mode that would
    make the whole feature worse than the duplication it removes."""
    bridge = SqliteMemoryBridge(tmp_db)

    await bridge.store("Bakir prefers root-cause fixes.", LANE)
    await bridge.store("The dentist is on Preston Road in Plano.", LANE)

    rows = await tmp_db.fetch_all("SELECT content FROM staged_facts")
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_a_gate_failure_never_costs_the_write(tmp_db: Any, monkeypatch: Any) -> None:
    """B5. Remembering is the point; deduplicating is the improvement. If the
    gate raises, the fact must still be stored — the opposite trade would lose
    user data to protect a tidiness feature."""
    def _boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("gate exploded")

    monkeypatch.setattr("stackowl.memory.sqlite_bridge.should_remember", _boom)
    bridge = SqliteMemoryBridge(tmp_db)

    await bridge.store("do not lose me", LANE)

    rows = await tmp_db.fetch_all("SELECT content FROM staged_facts")
    assert len(rows) == 1
    assert rows[0]["content"] == "do not lose me"
