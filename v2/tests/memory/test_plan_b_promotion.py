"""Plan B F6 — corroborate-then-commit promotion tests.

Verifies that:
- conversation_fact promotes at reinforcement_count >= 1 (lower threshold).
- conversation_fact at reinforcement_count 0 does NOT promote.
- Other source types (e.g. 'manual') still require the strict reinforcement_required=3.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from stackowl.db.pool import DbPool
from stackowl.memory.fact_promoter import FactPromoter
from stackowl.memory.sqlite_bridge import SqliteMemoryBridge

pytestmark = pytest.mark.asyncio


async def _insert_staged_raw(
    db: DbPool,
    *,
    fact_id: str,
    content: str = "a fact",
    source_type: str = "conversation_fact",
    source_ref: str = "sess-test",
    confidence: float = 0.9,
    reinforcement_count: int = 0,
    status: str = "staged",
) -> None:
    """Insert a staged_facts row directly, bypassing the bridge (for unit tests)."""
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
            datetime.now(UTC).isoformat(),
            reinforcement_count,
            status,
            b"",
            None,
        ),
    )


async def test_conversation_fact_promotes_at_reinforcement_1(tmp_db: DbPool) -> None:
    """A conversation_fact with confidence>=0.8 and reinforcement_count=1 must promote."""
    fact_id = str(uuid.uuid4())
    await _insert_staged_raw(
        tmp_db,
        fact_id=fact_id,
        content="User lives in Baku",
        source_type="conversation_fact",
        confidence=0.9,
        reinforcement_count=1,
    )

    promoter = FactPromoter(
        tmp_db,
        confidence_threshold=0.8,
        reinforcement_required=3,
        conversation_fact_reinforcement_required=1,
    )
    promoted = await promoter.promote_eligible()
    assert promoted == 1

    rows = await tmp_db.fetch_all(
        "SELECT fact_id FROM committed_facts WHERE fact_id = ?", (fact_id,)
    )
    assert rows, "fact must be in committed_facts"

    # Verify recall works via bridge (FTS path)
    bridge = SqliteMemoryBridge(tmp_db)
    results = await bridge.recall("Baku", limit=5)
    assert any(r.fact_id == fact_id for r in results), (
        f"recall() must return the promoted fact; got {[r.fact_id for r in results]}"
    )


async def test_conversation_fact_not_promoted_at_reinforcement_0(tmp_db: DbPool) -> None:
    """A conversation_fact with reinforcement_count=0 must NOT promote (needs at least 1)."""
    fact_id = str(uuid.uuid4())
    await _insert_staged_raw(
        tmp_db,
        fact_id=fact_id,
        content="User likes Python",
        source_type="conversation_fact",
        confidence=0.9,
        reinforcement_count=0,
    )

    promoter = FactPromoter(
        tmp_db,
        confidence_threshold=0.8,
        reinforcement_required=3,
        conversation_fact_reinforcement_required=1,
    )
    promoted = await promoter.promote_eligible()
    assert promoted == 0

    rows = await tmp_db.fetch_all(
        "SELECT fact_id FROM committed_facts WHERE fact_id = ?", (fact_id,)
    )
    assert not rows, "fact must NOT be in committed_facts at reinforcement_count=0"


async def test_other_source_type_still_needs_3(tmp_db: DbPool) -> None:
    """A 'manual' fact at reinforcement_count=1 must NOT promote — still needs 3."""
    fact_id = str(uuid.uuid4())
    await _insert_staged_raw(
        tmp_db,
        fact_id=fact_id,
        content="User prefers dark mode",
        source_type="manual",
        confidence=0.9,
        reinforcement_count=1,
    )

    promoter = FactPromoter(
        tmp_db,
        confidence_threshold=0.8,
        reinforcement_required=3,
        conversation_fact_reinforcement_required=1,
    )
    promoted = await promoter.promote_eligible()
    assert promoted == 0

    rows = await tmp_db.fetch_all(
        "SELECT fact_id FROM committed_facts WHERE fact_id = ?", (fact_id,)
    )
    assert not rows, "manual fact at reinforcement_count=1 must NOT be promoted"
