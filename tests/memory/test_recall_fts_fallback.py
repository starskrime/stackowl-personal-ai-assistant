"""recall() surfaces a committed fact through FTS5.

WHAT THIS USED TO BE, and why it shrank rather than went away. The original bug
(P0-1) was that ``SqliteMemoryBridge.recall()`` guarded its semantic result with
``if semantic is not None:`` — but ``semantic_recall`` returned an empty LIST when
the LanceDB ``committed_facts`` table did not exist, and ``[] is not None`` is True,
so recall returned nothing and never reached the FTS5 fallback. The test wired a
LanceDB adapter at an empty directory to force that ``[]`` and proved the fallback
fired.

D08.2 removed the semantic path with LanceDB itself: it ranked vectors and then
hydrated content from ``committed_facts``, which has held 0 rows since migration
0112 and lost its last writer in seam 3 pass 4. There is no longer a semantic
result to mis-guard, so the SPECIFIC bug cannot recur — the ladder it fell down is
now the only rung.

What survives is the GUARANTEE underneath it: a committed fact is findable through
recall(), which is live code the registered ``memory`` tool calls on every search.
That is what this now asserts, without the empty-LanceDB scaffolding that existed
only to defeat a path that no longer exists.
"""

from __future__ import annotations

import uuid

import pytest

from stackowl.db.pool import DbPool
from stackowl.memory.sqlite_bridge import SqliteMemoryBridge
from tests.memory._committed_fact_fixture import insert_committed

pytestmark = pytest.mark.asyncio

# Distinctive token unlikely to collide with any other seeded fact.
_FACT_CONTENT = "The Otto Ninja starter robot kit ships with two servos"


async def test_recall_surfaces_a_committed_fact_via_fts5(tmp_db: DbPool) -> None:
    fact_id = str(uuid.uuid4())
    await insert_committed(
        tmp_db,
        fact_id=fact_id,
        content=_FACT_CONTENT,
        source_type="conversation_fact",
        source_ref="sess-fallback",
    )

    results = await SqliteMemoryBridge(tmp_db).recall("ninja robot", limit=5)

    assert results, "recall() returned nothing for a committed, FTS-matchable fact"
    assert any(r.fact_id == fact_id for r in results), (
        f"recall() must surface the seeded fact; got {[r.fact_id for r in results]}"
    )
    assert any("Ninja" in r.content for r in results), (
        "the recalled record's content must match the seeded fact"
    )


async def test_recall_returns_empty_rather_than_raising_on_no_match(
    tmp_db: DbPool,
) -> None:
    """The honest empty. A query with no match must be an empty list, not an
    exception — the `memory` tool reports "no matches" from exactly this."""
    assert await SqliteMemoryBridge(tmp_db).recall("nothing matches this", limit=5) == []
