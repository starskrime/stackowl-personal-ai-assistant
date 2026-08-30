"""ESC-69 interim — recall must see the memories that actually exist.

MEASURED 2026-08-30: `staged_facts` holds 361 rows, ALL status='staged', with
their `embedding` column populated and the newest written minutes before the
measurement. `committed_facts` holds ZERO, and nothing promotes staged ->
committed since the extractor was retired (D08.1 R5Q18). `recall()` reads only
committed, so 414 memory searches returned 0 archive hits and 97% returned
nothing at all.

Bakir's endgame (ESC-69) is "if new memory system work old one we can delete
memories". This is the INTERIM: make what exists reachable while the replacement
is built. It returns RAW CONVERSATION TURNS rather than distilled facts, which is
exactly what the retired extractor used to fix — a known and accepted trade,
recorded rather than hidden.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stackowl.db.pool import DbPool
from stackowl.memory.sqlite_helpers import staged_recall

pytestmark = pytest.mark.asyncio


@pytest.fixture()
async def db(tmp_path: Path, _migrated_template: Path):
    from tests.conftest import seed_migrated_db

    pool = DbPool(db_path=seed_migrated_db(tmp_path / "t.db", _migrated_template))
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


async def _stage(db: DbPool, fact_id: str, content: str) -> None:
    await db.execute(
        "INSERT INTO staged_facts (fact_id, content, source_type, source_ref, "
        "confidence, staged_at, status, owner_id) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (fact_id, content, "conversation", "ref", 1.0,
         "2026-08-30T00:00:00+00:00", "staged", "principal-default"),
    )


async def test_a_staged_fact_is_FOUND(db: DbPool) -> None:
    await _stage(db, "f1", "User: the deploy host is bastion-prod-7")
    hits = await staged_recall(db, "bastion", limit=10)
    assert [h.fact_id for h in hits] == ["f1"]
    assert "bastion-prod-7" in hits[0].content


async def test_a_non_match_returns_NOTHING(db: DbPool) -> None:
    """A search that matches nothing must return nothing — not everything. A
    substring scan with an empty needle is one bug away from dumping the store."""
    await _stage(db, "f1", "User: the deploy host is bastion-prod-7")
    assert await staged_recall(db, "kubernetes", limit=10) == []


async def test_an_EMPTY_query_returns_nothing_rather_than_the_whole_store(db: DbPool) -> None:
    await _stage(db, "f1", "anything at all")
    for q in ("", "   ", "%"):
        assert await staged_recall(db, q, limit=10) == [], f"query {q!r} leaked rows"


async def test_the_limit_is_honoured(db: DbPool) -> None:
    for i in range(12):
        await _stage(db, f"f{i}", f"shared token number {i}")
    assert len(await staged_recall(db, "shared", limit=5)) == 5


async def test_only_STAGED_rows_are_returned(db: DbPool) -> None:
    """A rejected fact is a decision, not a memory. Recalling it would resurrect
    something the platform already declined."""
    await _stage(db, "keep", "shared token keep")
    await _stage(db, "drop", "shared token drop")
    await db.execute("UPDATE staged_facts SET status='rejected' WHERE fact_id='drop'")
    assert [h.fact_id for h in await staged_recall(db, "shared", limit=10)] == ["keep"]


async def test_a_LIKE_metacharacter_in_the_query_is_not_a_wildcard(db: DbPool) -> None:
    """`%` and `_` are LIKE wildcards. An unescaped `_` would make "a_c" match
    "abc" — a user searching for a literal underscore would get noise, and a
    single `%` would match every row."""
    await _stage(db, "f1", "token abc here")
    await _stage(db, "f2", "token a_c here")
    hits = await staged_recall(db, "a_c", limit=10)
    assert [h.fact_id for h in hits] == ["f2"], "the underscore acted as a wildcard"


# --------------------------------------------------------------------------- #
# WIRING — staged_recall being correct proves nothing about recall() reaching it.
# --------------------------------------------------------------------------- #


async def test_recall_ITSELF_now_returns_staged_facts(db: DbPool) -> None:
    """The mutation target. Before this change recall() read only
    committed_facts, which has 0 rows, so it returned nothing on 414 of 414
    measured production searches."""
    from stackowl.memory.sqlite_bridge import SqliteMemoryBridge

    await _stage(db, "f1", "User: the deploy host is bastion-prod-7")
    bridge = SqliteMemoryBridge(db=db, embedding_registry=None)

    hits = await bridge.recall("bastion", limit=10)
    assert [h.fact_id for h in hits] == ["f1"]


async def test_recall_returns_nothing_when_nothing_matches(db: DbPool) -> None:
    """The counterweight: widening the source must not make recall answer
    everything. An always-answering recall is worse than an empty one, because
    the model believes it."""
    from stackowl.memory.sqlite_bridge import SqliteMemoryBridge

    await _stage(db, "f1", "User: the deploy host is bastion-prod-7")
    bridge = SqliteMemoryBridge(db=db, embedding_registry=None)

    assert await bridge.recall("kubernetes", limit=10) == []
