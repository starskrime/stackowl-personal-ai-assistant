"""D01.7 slice 3b part 5a — a fact authored ONCE must still reach recall.

THE TRAP THIS CLOSES. The promotion gate requires corroboration:
``reinforcement_count >= 3`` for any source type other than ``conversation_fact``
(which needs 1). That is the right rule for an EXTRACTED claim — a fact derived
twice is more likely true than one derived once.

It is the wrong rule for an AUTHORED artifact. The rollover summary (Q17) is
written exactly once per conversation boundary and is never re-derived, so its
reinforcement_count stays 0 forever. Staged under any existing source type it
would sit in ``staged_facts`` permanently, never promoted, never recalled — the
third dormant-feature trap found in this item, and the only one that would have
been completely silent: the summary would be written, the logs would say so, and
recall would never see it.

So the gate learns the distinction. These tests pin both halves: an authored-once
fact promotes with no corroboration, and nothing else's threshold moves.
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
    db: DbPool, *, fact_id: str, content: str, source_type: str,
    confidence: float = 0.9, reinforcement_count: int = 0,
    source_ref: str = "bakir",
) -> None:
    await db.execute(
        """INSERT INTO staged_facts (
               fact_id, content, source_type, source_ref, confidence,
               staged_at, reinforcement_count, status, embedding, embedding_model,
               trust
           ) VALUES (?, ?, ?, ?, ?, ?, ?, 'staged', ?, NULL, 'self')""",
        (fact_id, content, source_type, source_ref, confidence,
         datetime.now(UTC).isoformat(), reinforcement_count, b""),
    )


def _promoter(db: DbPool) -> FactPromoter:
    """The shipped defaults, so these tests fail if the defaults stop working."""
    return FactPromoter(
        db, confidence_threshold=0.8, reinforcement_required=3,
        conversation_fact_reinforcement_required=1,
    )


async def test_a_summary_promotes_without_corroboration(tmp_db: DbPool) -> None:
    """The headline: authored once, reinforcement 0, and it still reaches memory."""
    fact_id = str(uuid.uuid4())
    await _insert_staged_raw(
        tmp_db, fact_id=fact_id, source_type="conversation_summary",
        content="Decided to rebuild the session lifecycle before the frozen prompt.",
        reinforcement_count=0,
    )

    assert await _promoter(tmp_db).promote_eligible() == 1

    rows = await tmp_db.fetch_all(
        "SELECT fact_id FROM committed_facts WHERE fact_id = ?", (fact_id,)
    )
    assert rows, "an authored-once summary must be committed, not left staged forever"


async def test_a_promoted_summary_is_actually_recallable(tmp_db: DbPool) -> None:
    """Committed is not the goal; RECALLED is. The dormancy was invisible because
    every intermediate step reported success."""
    fact_id = str(uuid.uuid4())
    await _insert_staged_raw(
        tmp_db, fact_id=fact_id, source_type="conversation_summary",
        content="Agreed the rollover summary must be narrative, not fact extraction.",
    )
    await _promoter(tmp_db).promote_eligible()

    results = await SqliteMemoryBridge(tmp_db).recall("narrative", limit=5)
    assert any(r.fact_id == fact_id for r in results), (
        f"recall must return the summary; got {[r.fact_id for r in results]}"
    )


async def test_an_extracted_fact_still_needs_corroboration(tmp_db: DbPool) -> None:
    """The existing rule must not move. A conversation_fact at 0 stays staged."""
    fact_id = str(uuid.uuid4())
    await _insert_staged_raw(
        tmp_db, fact_id=fact_id, source_type="conversation_fact",
        content="User likes Python", reinforcement_count=0,
    )
    assert await _promoter(tmp_db).promote_eligible() == 0


async def test_a_manual_fact_still_needs_three(tmp_db: DbPool) -> None:
    """The strict branch must not move either — this is the regression that would
    make every unrelated fact type promote on first sight."""
    fact_id = str(uuid.uuid4())
    await _insert_staged_raw(
        tmp_db, fact_id=fact_id, source_type="manual",
        content="A manual note", reinforcement_count=1,
    )
    assert await _promoter(tmp_db).promote_eligible() == 0


async def test_a_summary_below_the_confidence_gate_is_still_refused(
    tmp_db: DbPool,
) -> None:
    """Corroboration is waived; HONESTY is not. Authored-once buys exemption from
    needing a second sighting, never from the confidence gate."""
    fact_id = str(uuid.uuid4())
    await _insert_staged_raw(
        tmp_db, fact_id=fact_id, source_type="conversation_summary",
        content="A low-confidence guess", confidence=0.5,
    )
    assert await _promoter(tmp_db).promote_eligible() == 0
