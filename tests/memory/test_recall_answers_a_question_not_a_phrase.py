"""Memory recall must survive being asked a QUESTION.

MEASURED 2026-09-03 against the live database, through the real recall path:

    query                          hits
    "jobmarket"                       5
    "jobmarket agent"                 0
    "agents"                          5
    "what agents do I have"           0

``staged_recall`` matches ``content LIKE '%<THE ENTIRE QUERY>%'`` — the whole
query as one literal phrase. It fires only when the user's exact wording appears
verbatim inside a stored fact, so a single keyword works and **any natural
question returns nothing**. A fact whose content begins "The jobmarket scout is
the existing daily 2 PM CT agent" is invisible to "what agents do I have".

This is the entire live archive path: ``committed_facts`` has held 0 rows since
migration 0112, so ``fts_recall`` returns nothing and everything depends on this
scan.

WHY IT WAS LIKE THAT, AND WHY THAT REASON EXPIRED. The substring scan was the
ESC-69 interim, written when ``staged_facts`` had NO embeddings — a fact recorded
in its own docstring at the time. A separate fix on 2026-08-25 started writing
them ("this bridge held an embedding registry and never used it"), and recall was
never switched over. MEASURED: 205 of the 230 staged rows (89%) now carry a
384-dimension all-MiniLM-L6-v2 vector. The store is vectorised and searched by
literal phrase.

THE PATTERN IS NOT INVENTED HERE. ``recall()``'s own docstring names the
replacement — "the replacement pattern is already proven in
learning/lessons_store.py: embeddings as SQLite BLOBs plus a cached numpy scan" —
and this copies it, including its two safety properties: a query only ever sees
rows of its OWN embedding dimension, and an embedder that fails returns no hits
rather than crashing the turn.

SEMANTIC DOES NOT REPLACE THE SCAN, IT PRECEDES IT. 25 of the 230 rows carry no
vector at all, and deleting the substring path would make exactly those
unreachable — the same "made unreachable and called it a cleanup" mistake ESC-69
exists to correct. Both run; semantic first, substring tops up.
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from stackowl.db.pool import DbPool
from stackowl.memory.sqlite_helpers import staged_recall, staged_semantic_recall

pytestmark = pytest.mark.asyncio


def _vec(*values: float) -> bytes:
    """A little-endian float32 blob, the shape the embedder writes."""
    return struct.pack(f"<{len(values)}f", *values)


@pytest.fixture()
async def db(tmp_path: Path, _migrated_template: Path):
    from tests.conftest import seed_migrated_db

    pool = DbPool(db_path=seed_migrated_db(tmp_path / "t.db", _migrated_template))
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


async def _stage(
    db: DbPool, fact_id: str, content: str, embedding: bytes | None = None,
) -> None:
    await db.execute(
        "INSERT INTO staged_facts (fact_id, content, source_type, source_ref, "
        "confidence, staged_at, status, owner_id, embedding, embedding_model) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (fact_id, content, "conversation", "ref", 1.0,
         "2026-09-03T00:00:00+00:00", "staged", "principal-default",
         embedding, "all-MiniLM-L6-v2" if embedding else None),
    )


# --------------------------------------------------------------------------- #
# The regression                                                               #
# --------------------------------------------------------------------------- #


async def test_a_question_finds_the_fact_its_words_do_not_literally_match(
    db: DbPool,
) -> None:
    """THE DEFECT. The stored fact and the question share no verbatim phrase."""
    await _stage(db, "f-near", "The jobmarket scout runs daily at 2 PM CT",
                 _vec(1.0, 0.0, 0.0))
    await _stage(db, "f-far", "Coffee grounds belong in the compost bin",
                 _vec(0.0, 0.0, 1.0))

    # The question's vector points at the first fact, not the second.
    hits = await staged_semantic_recall(db, [0.96, 0.28, 0.0], limit=5)

    assert hits, "a question that matches no literal phrase returned nothing"
    assert hits[0].fact_id == "f-near"


async def test_the_substring_scan_still_works_for_a_literal_keyword(
    db: DbPool,
) -> None:
    """The old path is kept, not replaced: 25 of 230 live rows have NO vector,
    and dropping the scan would make exactly those unreachable."""
    await _stage(db, "f-1", "The bastion host is 192.168.1.81")
    assert [h.fact_id for h in await staged_recall(db, "bastion", limit=10)] == ["f-1"]


# --------------------------------------------------------------------------- #
# What must not go wrong                                                       #
# --------------------------------------------------------------------------- #


async def test_a_row_of_a_different_embedding_dimension_is_never_compared(
    db: DbPool,
) -> None:
    """Embedder drift returns NO hits rather than confident nonsense — the same
    rule lessons_store enforces. Comparing a 3-dim query against a 4-dim row is
    not a near miss, it is a different space."""
    await _stage(db, "f-4d", "written under a different embedder",
                 _vec(1.0, 0.0, 0.0, 0.0))
    assert await staged_semantic_recall(db, [1.0, 0.0, 0.0], limit=5) == []


async def test_a_row_with_no_vector_is_skipped_not_crashed_on(db: DbPool) -> None:
    """89% of live rows are embedded; the rest must not take recall down."""
    await _stage(db, "f-none", "no vector here", None)
    await _stage(db, "f-vec", "has a vector", _vec(1.0, 0.0, 0.0))
    hits = await staged_semantic_recall(db, [1.0, 0.0, 0.0], limit=5)
    assert [h.fact_id for h in hits] == ["f-vec"]


async def test_an_empty_query_vector_returns_nothing(db: DbPool) -> None:
    """``_embed_content`` hands back None on failure. Searching on that must be
    an empty result, never an exception on the turn path — and never the whole
    store, which is the shape that turns a search into a dump."""
    await _stage(db, "f-1", "anything", _vec(1.0, 0.0, 0.0))
    assert await staged_semantic_recall(db, [], limit=5) == []
    assert await staged_semantic_recall(db, None, limit=5) == []


async def test_only_staged_rows_are_recalled(db: DbPool) -> None:
    """A rejected fact is a decision, not a memory — the invariant the substring
    scan already holds, which the semantic path must not quietly drop."""
    await _stage(db, "f-ok", "kept", _vec(1.0, 0.0, 0.0))
    await _stage(db, "f-no", "rejected", _vec(1.0, 0.0, 0.0))
    await db.execute("UPDATE staged_facts SET status='rejected' WHERE fact_id='f-no'")
    assert [h.fact_id for h in await staged_semantic_recall(db, [1.0, 0.0, 0.0], 5)] == ["f-ok"]


async def test_recall_is_owner_scoped(db: DbPool) -> None:
    """staged_facts is owner-governed; a semantic path that forgot the predicate
    would be a cross-tenant read, which the tenancy tripwire exists to stop."""
    await _stage(db, "f-mine", "mine", _vec(1.0, 0.0, 0.0))
    await db.execute(
        "INSERT INTO staged_facts (fact_id, content, source_type, source_ref, "
        "confidence, staged_at, status, owner_id, embedding, embedding_model) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("f-theirs", "theirs", "conversation", "ref", 1.0,
         "2026-09-03T00:00:00+00:00", "staged", "someone-else",
         _vec(1.0, 0.0, 0.0), "all-MiniLM-L6-v2"),
    )
    assert [h.fact_id for h in await staged_semantic_recall(db, [1.0, 0.0, 0.0], 5)] == ["f-mine"]


# --------------------------------------------------------------------------- #
# Through recall() itself — the helper tests cannot see the top-up quota        #
# --------------------------------------------------------------------------- #


async def test_recall_still_reaches_a_row_that_has_no_vector(db: DbPool) -> None:
    """THE REGRESSION THE FIX COULD HAVE CAUSED.

    Semantic recall always fills whatever budget it is given, and 205 of the 230
    live rows are embedded. If the substring scan is handed ``limit -
    len(records)`` after that, it gets ZERO — and the 25 rows with no vector
    become permanently unreachable. That is precisely the failure ESC-69 was
    opened to fix, reintroduced by its own successor.

    Exercised through ``recall()`` rather than the helper, because the quota only
    exists at that level: the helper tests above would stay green while the live
    path silently dropped every unembedded row."""
    from stackowl.memory.sqlite_bridge import SqliteMemoryBridge

    # Enough embedded rows to swallow the whole budget on their own.
    for i in range(12):
        await _stage(db, f"vec-{i}", f"embedded note number {i}", _vec(1.0, 0.0, 0.0))
    await _stage(db, "novec", "this row has no vector at all", None)

    bridge = SqliteMemoryBridge(db=db)

    async def _fake_embed(_content: str):
        return [1.0, 0.0, 0.0], "all-MiniLM-L6-v2"

    bridge._embed_content = _fake_embed  # type: ignore[method-assign]
    records = await bridge.recall("this row has no vector at all", limit=5)

    assert "novec" in {r.fact_id for r in records}, (
        "the unembedded row was crowded out by semantic hits — the substring "
        "reserve is gone and those rows are unreachable again"
    )


async def test_recall_skips_semantic_when_the_embedder_degraded(db: DbPool) -> None:
    """THE SILENT ONE. EmbeddingRegistry falls back to HashEmbeddingProvider on
    any load failure, and that fallback is ALSO 384-dimension
    ("hash-v1-384d") — so a dimension check cannot see it and cosine against
    MiniLM rows is noise. The bridge's own dedup gate already refuses to compare
    across models for this reason; recall must too, or a degrade injects random
    "memories" into every prompt with nothing to notice."""
    from stackowl.memory.sqlite_bridge import SqliteMemoryBridge

    await _stage(db, "mini", "a real memory", _vec(1.0, 0.0, 0.0))
    bridge = SqliteMemoryBridge(db=db)

    async def _degraded(_content: str):
        return [1.0, 0.0, 0.0], "hash-v1-384d"

    bridge._embed_content = _degraded  # type: ignore[method-assign]
    records = await bridge.recall("a real memory", limit=5)

    # It may still be found by the SUBSTRING path — that is fine and correct.
    # What must not happen is a semantic hit scored across two different models.
    assert all(r.embedding_model != "hash-v1-384d" for r in records)
