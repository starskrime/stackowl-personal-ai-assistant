"""Settle-window age filter — Clock-driven eligibility gate.

DreamWorker only consolidates staged data older than ``settle_minutes`` so
in-flight conversation turns aren't promoted/mined prematurely.

Verifies:
- FactPromoter with settle=15 and a FixedClock at T: a conversation_fact staged
  at T-5min is NOT promoted; staged at T-20min IS promoted.
- ConversationMiner with settle=15: a turn staged 5 min ago is skipped; a turn
  staged 20 min ago is mined.
- recent_conversation_turns(staged_before=None) is the unchanged default path.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from stackowl.db.pool import DbPool
from stackowl.memory.conversation_miner import ConversationMiner
from stackowl.memory.fact_promoter import FactPromoter
from stackowl.memory.models import StagedFact
from stackowl.memory.sqlite_bridge import SqliteMemoryBridge

from tests.infra.test_clock import FixedClock

pytestmark = pytest.mark.asyncio

_T = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)


async def _insert_staged_raw(
    db: DbPool,
    *,
    fact_id: str,
    staged_at: datetime,
    content: str = "a fact",
    source_type: str = "conversation_fact",
    source_ref: str = "sess-test",
    confidence: float = 0.9,
    reinforcement_count: int = 1,
    status: str = "staged",
) -> None:
    await db.execute(
        """INSERT INTO staged_facts (
               fact_id, content, source_type, source_ref, confidence,
               staged_at, reinforcement_count, status, embedding, embedding_model
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            fact_id,
            content,
            source_type,
            source_ref,
            confidence,
            staged_at.isoformat(),
            reinforcement_count,
            status,
            b"",
            None,
        ),
    )


# ---------------------------------------------------------------------------
# FactPromoter settle window
# ---------------------------------------------------------------------------


async def test_promoter_skips_fact_inside_settle_window(tmp_db: DbPool) -> None:
    fid = str(uuid.uuid4())
    await _insert_staged_raw(tmp_db, fact_id=fid, staged_at=_T - timedelta(minutes=5))

    promoter = FactPromoter(
        tmp_db,
        confidence_threshold=0.8,
        conversation_fact_reinforcement_required=1,
        clock=FixedClock(_T),
        settle_minutes=15,
    )
    promoted = await promoter.promote_eligible()
    assert promoted == 0

    rows = await tmp_db.fetch_all(
        "SELECT fact_id FROM committed_facts WHERE fact_id = ?", (fid,)
    )
    assert not rows, "fact staged 5 min ago must not promote (inside settle window)"


async def test_promoter_promotes_fact_past_settle_window(tmp_db: DbPool) -> None:
    fid = str(uuid.uuid4())
    await _insert_staged_raw(tmp_db, fact_id=fid, staged_at=_T - timedelta(minutes=20))

    promoter = FactPromoter(
        tmp_db,
        confidence_threshold=0.8,
        conversation_fact_reinforcement_required=1,
        clock=FixedClock(_T),
        settle_minutes=15,
    )
    promoted = await promoter.promote_eligible()
    assert promoted == 1

    rows = await tmp_db.fetch_all(
        "SELECT fact_id FROM committed_facts WHERE fact_id = ?", (fid,)
    )
    assert rows, "fact staged 20 min ago must promote (past settle window)"


# ---------------------------------------------------------------------------
# ConversationMiner settle window
# ---------------------------------------------------------------------------


class _StubExtractor:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def extract(self, messages: list[object], session_id: str) -> list[StagedFact]:
        self.calls.append(session_id)
        return [
            StagedFact(
                content=f"fact about {session_id}",
                source_type="conversation_fact",
                source_ref=session_id,
                confidence=0.9,
            )
        ]


async def _store_turn(db: DbPool, session_id: str, staged_at: datetime) -> None:
    await db.execute(
        """INSERT INTO staged_facts (
               fact_id, content, source_type, source_ref, confidence,
               staged_at, reinforcement_count, status, embedding, embedding_model
           ) VALUES (?, ?, 'conversation', ?, 0.5, ?, 0, 'staged', ?, ?)""",
        (
            str(uuid.uuid4()),
            "User: I live in Baku\n\nAssistant: Noted.",
            session_id,
            staged_at.isoformat(),
            b"",
            None,
        ),
    )


async def test_miner_skips_turn_inside_settle_window(tmp_db: DbPool) -> None:
    bridge = SqliteMemoryBridge(tmp_db)
    await _store_turn(tmp_db, "s1", _T - timedelta(minutes=5))

    ex = _StubExtractor()
    miner = ConversationMiner(
        db=tmp_db, extractor=ex, bridge=bridge, message_limit=20,
        clock=FixedClock(_T), settle_minutes=15,
    )
    count = await miner.mine_session("s1")
    assert count == 0
    assert ex.calls == [], "no turns past settle window — extractor must not be called"


async def test_miner_mines_turn_past_settle_window(tmp_db: DbPool) -> None:
    bridge = SqliteMemoryBridge(tmp_db)
    await _store_turn(tmp_db, "s1", _T - timedelta(minutes=20))

    ex = _StubExtractor()
    miner = ConversationMiner(
        db=tmp_db, extractor=ex, bridge=bridge, message_limit=20,
        clock=FixedClock(_T), settle_minutes=15,
    )
    count = await miner.mine_session("s1")
    assert count == 1
    assert ex.calls == ["s1"]


# ---------------------------------------------------------------------------
# Default short-term-recall path unchanged
# ---------------------------------------------------------------------------


async def test_recent_conversation_turns_default_path_unchanged(tmp_db: DbPool) -> None:
    """staged_before=None must return all turns regardless of age (short-term recall)."""
    bridge = SqliteMemoryBridge(tmp_db)
    await _store_turn(tmp_db, "s1", _T - timedelta(minutes=1))
    await _store_turn(tmp_db, "s1", _T - timedelta(minutes=100))

    turns = await bridge.recent_conversation_turns(session_id="s1", limit=10)
    assert len(turns) == 2, "default path must not filter by age"
