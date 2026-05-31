"""Plan B Task 1 — ConversationMiner extracts long-term facts from staged conversation turns.

RC-A fix: conversation turns are stored in staged_facts(source_type='conversation') but
recall() reads only committed_facts, which is empty. ConversationMiner runs the
FactExtractor over recent turns per session and stages facts as source_type='conversation_fact'.
Mining must be IDEMPOTENT — same turns re-mined do not produce duplicate facts.
"""

from __future__ import annotations

import pytest

from stackowl.db.pool import DbPool
from stackowl.memory.conversation_miner import ConversationMiner
from stackowl.memory.models import StagedFact
from stackowl.memory.sqlite_bridge import SqliteMemoryBridge

pytestmark = pytest.mark.asyncio


class _StubExtractor:
    """Synchronous-but-awaitable stub that returns one fact per session."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    async def extract(self, messages: list[object], session_id: str) -> list[StagedFact]:
        self.calls.append((session_id, len(messages)))
        return [
            StagedFact(
                content=f"fact about {session_id}",
                source_type="conversation_fact",
                source_ref=session_id,
                confidence=0.9,
            )
        ]


# ---------------------------------------------------------------------------
# Tests — use the `tmp_db` fixture from conftest (migrations run, pool open)
# ---------------------------------------------------------------------------


async def test_mine_session_extracts_and_stages(tmp_db: DbPool) -> None:
    """mine_session() stores exactly one extracted fact for a session with one turn."""
    bridge = SqliteMemoryBridge(tmp_db)
    await bridge.store("User: I live in Baku\n\nAssistant: Noted.", "s1")

    miner = ConversationMiner(db=tmp_db, extractor=_StubExtractor(), bridge=bridge, message_limit=20)
    count = await miner.mine_session("s1")

    assert count == 1
    rows = await tmp_db.fetch_all(
        "SELECT content FROM staged_facts WHERE source_type='conversation_fact' AND source_ref=?",
        ("s1",),
    )
    assert any("fact about s1" in r["content"] for r in rows)


async def test_mine_session_is_idempotent(tmp_db: DbPool) -> None:
    """Re-mining the same session produces no new rows (content dedup)."""
    bridge = SqliteMemoryBridge(tmp_db)
    await bridge.store("User: I live in Baku\n\nAssistant: Noted.", "s1")

    miner = ConversationMiner(db=tmp_db, extractor=_StubExtractor(), bridge=bridge, message_limit=20)
    first = await miner.mine_session("s1")
    assert first == 1

    second = await miner.mine_session("s1")
    assert second == 0  # content dedup -> nothing new staged

    rows = await tmp_db.fetch_all(
        "SELECT count(*) AS n FROM staged_facts WHERE source_type='conversation_fact' AND source_ref='s1'",
    )
    assert rows[0]["n"] == 1


async def test_mine_all_iterates_distinct_sessions(tmp_db: DbPool) -> None:
    """mine_all() processes every distinct session_id with conversation turns."""
    bridge = SqliteMemoryBridge(tmp_db)
    await bridge.store("User: a\n\nAssistant: b", "s1")
    await bridge.store("User: c\n\nAssistant: d", "s2")

    ex = _StubExtractor()
    miner = ConversationMiner(db=tmp_db, extractor=ex, bridge=bridge, message_limit=20)
    total = await miner.mine_all()

    assert total == 2
    assert {c[0] for c in ex.calls} == {"s1", "s2"}
