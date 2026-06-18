"""Phase 2 — semantic dedup/reinforcement in ConversationMiner.

The miner historically deduped/reinforced staged ``conversation_fact`` rows by
EXACT content string. A reworded LLM re-extraction of the same fact
("User lives in Baku" vs "The user is based in Baku") would not match and stage
a near-duplicate, breaking the corroborate-then-commit design.

These tests pin the SEMANTIC behaviour: a new fact whose embedding is close
(cosine >= threshold) to an existing staged fact for the same ``source_ref``
REINFORCES that fact rather than staging a duplicate. Dissimilar embeddings
stage a new row. When embeddings are missing the miner falls back to the
existing exact-content match (no crash).

Embeddings are constructed directly here (near-parallel vs orthogonal vectors)
so the test does not depend on a real embedding model.
"""

from __future__ import annotations

import logging

import pytest

from stackowl.db.pool import DbPool
from stackowl.memory.conversation_miner import ConversationMiner
from stackowl.memory.models import StagedFact
from stackowl.memory.sqlite_bridge import SqliteMemoryBridge

pytestmark = pytest.mark.asyncio


class _ScriptedExtractor:
    """Returns a pre-built list of facts on each ``extract`` call (one list per call)."""

    def __init__(self, scripts: list[list[StagedFact]]) -> None:
        self._scripts = scripts
        self._call = 0

    async def extract(self, messages: list[object], session_id: str) -> list[StagedFact]:
        facts = self._scripts[self._call]
        self._call += 1
        return facts


async def _staged_rows(db: DbPool, session_id: str) -> list[dict]:
    return await db.fetch_all(
        "SELECT content, reinforcement_count FROM staged_facts "
        "WHERE source_type='conversation_fact' AND source_ref=? ORDER BY staged_at",
        (session_id,),
    )


async def test_reworded_fact_reinforces_not_duplicates(tmp_db: DbPool) -> None:
    """A reworded re-extraction (different text, ~identical embedding) reinforces.

    Pass 1 stages content "User lives in Baku" with embedding E1.
    Pass 2 returns "The user is based in Baku" with embedding ~parallel to E1.
    Expect: still ONE row, reinforcement_count bumped to 1, no new row.
    """
    bridge = SqliteMemoryBridge(tmp_db)
    await bridge.store("User: I live in Baku\n\nAssistant: Noted.", "s1")

    e1 = [1.0, 0.0, 0.0, 0.0]
    e1_reworded = [0.999, 0.01, 0.0, 0.0]  # cosine ~0.9999 >= 0.92

    extractor = _ScriptedExtractor(
        [
            [StagedFact(content="User lives in Baku", source_type="conversation_fact",
                        source_ref="s1", confidence=0.9, embedding=e1)],
            [StagedFact(content="The user is based in Baku", source_type="conversation_fact",
                        source_ref="s1", confidence=0.9, embedding=e1_reworded)],
        ]
    )
    miner = ConversationMiner(db=tmp_db, extractor=extractor, bridge=bridge, message_limit=20)

    first = await miner.mine_session("s1")
    assert first == 1

    second = await miner.mine_session("s1")
    assert second == 0, "reworded duplicate must reinforce, not stage a new row"

    rows = await _staged_rows(tmp_db, "s1")
    assert len(rows) == 1, f"expected exactly one staged row, got {[r['content'] for r in rows]}"
    assert rows[0]["content"] == "User lives in Baku", "original row content preserved"
    assert rows[0]["reinforcement_count"] == 1, "original fact must be reinforced"


async def test_dissimilar_fact_stages_new(tmp_db: DbPool) -> None:
    """An orthogonal embedding (different fact) stages a NEW row → 2 rows total."""
    bridge = SqliteMemoryBridge(tmp_db)
    await bridge.store("User: hi\n\nAssistant: hello", "s1")

    e1 = [1.0, 0.0, 0.0, 0.0]
    e_orthogonal = [0.0, 1.0, 0.0, 0.0]  # cosine 0.0 < 0.92

    extractor = _ScriptedExtractor(
        [
            [StagedFact(content="User lives in Baku", source_type="conversation_fact",
                        source_ref="s1", confidence=0.9, embedding=e1)],
            [StagedFact(content="User works as an engineer", source_type="conversation_fact",
                        source_ref="s1", confidence=0.9, embedding=e_orthogonal)],
        ]
    )
    miner = ConversationMiner(db=tmp_db, extractor=extractor, bridge=bridge, message_limit=20)

    assert await miner.mine_session("s1") == 1
    assert await miner.mine_session("s1") == 1, "dissimilar fact must stage a new row"

    rows = await _staged_rows(tmp_db, "s1")
    assert len(rows) == 2, f"expected two distinct staged rows, got {[r['content'] for r in rows]}"
    assert all(r["reinforcement_count"] == 0 for r in rows)


async def test_falls_back_to_exact_match_when_embedding_missing(
    tmp_db: DbPool, caplog
) -> None:
    """When embeddings are None, exact-content dedup still works (no crash)."""
    bridge = SqliteMemoryBridge(tmp_db)
    await bridge.store("User: I live in Baku\n\nAssistant: Noted.", "s1")

    extractor = _ScriptedExtractor(
        [
            [StagedFact(content="User lives in Baku", source_type="conversation_fact",
                        source_ref="s1", confidence=0.9, embedding=None)],
            # Exact same content, still no embedding → must reinforce via fallback.
            [StagedFact(content="User lives in Baku", source_type="conversation_fact",
                        source_ref="s1", confidence=0.9, embedding=None)],
        ]
    )
    miner = ConversationMiner(db=tmp_db, extractor=extractor, bridge=bridge, message_limit=20)

    with caplog.at_level(logging.DEBUG, logger="stackowl.memory"):
        assert await miner.mine_session("s1") == 1
        assert await miner.mine_session("s1") == 0

    rows = await _staged_rows(tmp_db, "s1")
    assert len(rows) == 1, "exact-match fallback must reinforce, not duplicate"
    assert rows[0]["reinforcement_count"] == 1


async def test_missing_embedding_different_content_stages_new(tmp_db: DbPool) -> None:
    """Fallback path: no embedding + different content → new row (no false merge)."""
    bridge = SqliteMemoryBridge(tmp_db)
    await bridge.store("User: hi\n\nAssistant: hello", "s1")

    extractor = _ScriptedExtractor(
        [
            [StagedFact(content="User lives in Baku", source_type="conversation_fact",
                        source_ref="s1", confidence=0.9, embedding=None)],
            [StagedFact(content="User lives in Berlin", source_type="conversation_fact",
                        source_ref="s1", confidence=0.9, embedding=None)],
        ]
    )
    miner = ConversationMiner(db=tmp_db, extractor=extractor, bridge=bridge, message_limit=20)

    assert await miner.mine_session("s1") == 1
    assert await miner.mine_session("s1") == 1

    rows = await _staged_rows(tmp_db, "s1")
    assert len(rows) == 2
