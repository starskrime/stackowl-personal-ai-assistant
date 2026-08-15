"""The lessons corpus in SQLite, ranked by numpy — replacing the LanceDB adapter.

WHY IT MOVED. Bakir, 2026-08-14: LanceDB is heavy and does not support all
platforms. Measured: `lancedb` 100MB + its `pyarrow` requirement 136MB = 236MB, for
a corpus of 3,680 rows x 384 dims = 5.4MB. A brute-force scan over that is one
matmul, ~1.4M FLOPs.

THE CONTRACT THAT MATTERS MOST IS NUMERIC, not structural. `heuristic_ranking`
scores hits as ``similarity + c * quality * sqrt(ln N / evidence)`` — so the SCALE
of `similarity` is coupled to a tuned constant. Swapping the distance metric would
silently re-balance ranking while every test still passed.

So the metric was measured against the live LanceDB table rather than assumed:
``_distance`` is L2 SQUARED (0.510582 for a pair whose L2 is 0.714550), and the
vectors are unit-norm. The adapter therefore reproduces ``1 / (1 + ||q - e||^2)``
exactly, and `test_similarity_matches_the_lancedb_formula_exactly` pins it.
"""

from __future__ import annotations

import math

import pytest

from stackowl.db.pool import DbPool
from stackowl.learning.lesson import Lesson

pytestmark = pytest.mark.asyncio


def _lesson(lid: str, vec: list[float], *, source_type: str = "reflection") -> Lesson:
    return Lesson(
        lesson_id=lid,
        source_type=source_type,  # type: ignore[arg-type]
        source_ref=f"ref-{lid}",
        content=f"content of {lid}",
        embedding=vec,
        metadata={"quality": 0.5},
    )


def _store(db: DbPool):
    from stackowl.learning.lessons_store import SqliteLessonsStore

    return SqliteLessonsStore(db)


class TestRoundTrip:
    async def test_a_published_lesson_comes_back_from_search(self, tmp_db: DbPool) -> None:
        store = _store(tmp_db)
        await store.publish(_lesson("a", [1.0, 0.0, 0.0]))

        hits = await store.search([1.0, 0.0, 0.0], limit=5)

        assert [h.lesson_id for h in hits] == ["a"]
        assert hits[0].content == "content of a"
        assert hits[0].source_ref == "ref-a"

    async def test_metadata_survives_the_round_trip(self, tmp_db: DbPool) -> None:
        """Ranking reads `quality` and `evidence_count` out of metadata, so a
        dropped dict is a silently worse ranking rather than a visible failure."""
        store = _store(tmp_db)
        await store.publish(_lesson("a", [1.0, 0.0, 0.0]))

        hits = await store.search([1.0, 0.0, 0.0], limit=5)

        assert hits[0].metadata == {"quality": 0.5}

    async def test_publishing_the_same_id_twice_updates_rather_than_duplicates(
        self, tmp_db: DbPool
    ) -> None:
        """lesson_id is "<source>:<source_ref>" by convention, so a re-mined lesson
        re-publishes under the SAME id. Two rows would double its weight in recall."""
        store = _store(tmp_db)
        await store.publish(_lesson("a", [1.0, 0.0, 0.0]))
        updated = _lesson("a", [0.0, 1.0, 0.0])
        object.__setattr__(updated, "content", "revised content")
        await store.publish(updated)

        hits = await store.search([0.0, 1.0, 0.0], limit=5)

        assert len(hits) == 1, f"duplicate rows for one lesson_id: {hits!r}"
        assert hits[0].content == "revised content"

    async def test_delete_removes_it(self, tmp_db: DbPool) -> None:
        store = _store(tmp_db)
        await store.publish(_lesson("a", [1.0, 0.0, 0.0]))
        await store.delete("a")

        assert await store.search([1.0, 0.0, 0.0], limit=5) == []


class TestRanking:
    async def test_nearest_first(self, tmp_db: DbPool) -> None:
        store = _store(tmp_db)
        await store.publish(_lesson("near", [1.0, 0.0, 0.0]))
        await store.publish(_lesson("far", [0.0, 1.0, 0.0]))

        hits = await store.search([0.99, 0.01, 0.0], limit=5)

        assert [h.lesson_id for h in hits] == ["near", "far"]

    async def test_limit_is_respected(self, tmp_db: DbPool) -> None:
        store = _store(tmp_db)
        for i in range(10):
            await store.publish(_lesson(f"l{i}", [1.0, float(i) / 10, 0.0]))

        assert len(await store.search([1.0, 0.0, 0.0], limit=3)) == 3

    async def test_similarity_matches_the_lancedb_formula_exactly(
        self, tmp_db: DbPool
    ) -> None:
        """THE numeric contract, pinned.

        LanceDB returned ``_distance`` as SQUARED L2 (measured on the live table:
        0.510582 for a pair whose L2 is 0.714550), and the adapter turned it into
        ``1 / (1 + distance)``. `heuristic_ranking` adds an exploration term to
        that value with a tuned constant, so a different scale would re-balance
        ranking invisibly. This asserts the exact number, not merely the order.
        """
        store = _store(tmp_db)
        await store.publish(_lesson("a", [1.0, 0.0, 0.0]))

        hits = await store.search([0.0, 1.0, 0.0], limit=1)

        # squared L2 between the unit vectors is 2.0 -> 1/(1+2) = 1/3
        assert hits[0].similarity == pytest.approx(1.0 / 3.0), hits[0].similarity

    async def test_an_identical_vector_scores_one(self, tmp_db: DbPool) -> None:
        store = _store(tmp_db)
        await store.publish(_lesson("a", [0.6, 0.8, 0.0]))

        hits = await store.search([0.6, 0.8, 0.0], limit=1)

        assert hits[0].similarity == pytest.approx(1.0)


class TestFiltering:
    async def test_source_filter_selects_one_tier(self, tmp_db: DbPool) -> None:
        store = _store(tmp_db)
        await store.publish(_lesson("r", [1.0, 0.0, 0.0], source_type="reflection"))
        await store.publish(_lesson("s", [1.0, 0.0, 0.0], source_type="skill"))

        hits = await store.search([1.0, 0.0, 0.0], limit=5, source_filter="skill")

        assert [h.lesson_id for h in hits] == ["s"]

    async def test_a_quote_in_the_filter_cannot_break_the_query(
        self, tmp_db: DbPool
    ) -> None:
        """The old adapter built ``where(f"source_type = '{escaped}'")`` by hand.
        This one binds a parameter, so the escaping question does not arise —
        asserted rather than assumed, because a filter that silently matched
        everything would leak other tiers into a scoped search."""
        store = _store(tmp_db)
        await store.publish(_lesson("r", [1.0, 0.0, 0.0], source_type="reflection"))

        assert await store.search([1.0, 0.0, 0.0], source_filter="' OR 1=1 --") == []


class TestDegradesHonestly:
    async def test_an_empty_corpus_returns_empty_not_an_error(
        self, tmp_db: DbPool
    ) -> None:
        assert await _store(tmp_db).search([1.0, 0.0, 0.0], limit=5) == []

    async def test_an_empty_query_vector_returns_empty(self, tmp_db: DbPool) -> None:
        """Carried over from the LanceDB adapter, which guarded this explicitly:
        an embedder that failed hands back [], and searching on it must not be a
        crash on the turn path."""
        store = _store(tmp_db)
        await store.publish(_lesson("a", [1.0, 0.0, 0.0]))

        assert await store.search([], limit=5) == []

    async def test_a_dimension_mismatch_is_skipped_not_crashed(
        self, tmp_db: DbPool
    ) -> None:
        """A corpus written under one embedder and queried under another.

        The fact store hit exactly this (F062) and the honest answer is to skip
        the incomparable rows rather than crash the turn or — worse — compare
        truncated vectors and return confident nonsense.
        """
        store = _store(tmp_db)
        await store.publish(_lesson("wrong_dim", [1.0, 0.0]))
        await store.publish(_lesson("right_dim", [1.0, 0.0, 0.0]))

        hits = await store.search([1.0, 0.0, 0.0], limit=5)

        assert [h.lesson_id for h in hits] == ["right_dim"], hits

    async def test_publish_many_is_one_pass(self, tmp_db: DbPool) -> None:
        store = _store(tmp_db)
        n = await store.publish_many([_lesson(f"l{i}", [1.0, float(i), 0.0]) for i in range(5)])

        assert n == 5
        assert len(await store.search([1.0, 0.0, 0.0], limit=10)) == 5

    async def test_a_lesson_with_no_embedding_is_refused_not_stored(
        self, tmp_db: DbPool
    ) -> None:
        """A row with no vector can never be recalled, so storing one would be a
        write with no reader — the shape this programme keeps finding."""
        store = _store(tmp_db)

        with pytest.raises(ValueError):
            await store.publish(_lesson("novec", []))


class TestScale:
    async def test_the_whole_corpus_ranks_in_one_pass(self, tmp_db: DbPool) -> None:
        """The claim that justified dropping the ANN: brute force is fine at this
        size. 3,680 x 384 is the live corpus; this uses a tenth of it and asserts
        correctness at scale rather than timing, which would be flaky on a box
        that also runs the platform.
        """
        store = _store(tmp_db)
        dim = 384
        lessons = []
        for i in range(368):
            vec = [0.0] * dim
            vec[i % dim] = 1.0
            lessons.append(_lesson(f"l{i}", vec))
        await store.publish_many(lessons)

        target = [0.0] * dim
        target[7] = 1.0
        hits = await store.search(target, limit=3)

        assert hits[0].lesson_id == "l7", [h.lesson_id for h in hits]
        assert hits[0].similarity == pytest.approx(1.0)
        # every other basis vector is sqrt(2) away -> squared 2 -> 1/3
        assert all(math.isclose(h.similarity, 1 / 3, rel_tol=1e-6) for h in hits[1:])


class TestTheCache:
    """The corpus is cached in-process, so freshness has to be earned.

    Loading is the expensive half (35-70ms against ~7ms of numpy), which is why the
    cache exists at all. But a cache that misses a write is worse than no cache: the
    owl would keep recalling a lesson it had already revised, and nothing would say so.
    """

    async def test_a_new_lesson_is_visible_immediately(self, tmp_db: DbPool) -> None:
        store = _store(tmp_db)
        await store.publish(_lesson("a", [1.0, 0.0, 0.0]))
        assert len(await store.search([1.0, 0.0, 0.0], limit=5)) == 1

        await store.publish(_lesson("b", [1.0, 0.0, 0.0]))

        assert len(await store.search([1.0, 0.0, 0.0], limit=5)) == 2, (
            "an insert after a search was not seen — the cache went stale on COUNT"
        )

    async def test_an_IN_PLACE_revision_is_visible_immediately(
        self, tmp_db: DbPool
    ) -> None:
        """The case COUNT(*) alone cannot catch, and the reason updated_at exists.

        lesson_id is "<source>:<source_ref>", so a re-mined lesson upserts the SAME
        row: the row count does not move and neither does MAX(rowid). Without the
        updated_at stamp a cached corpus would serve the superseded text forever.
        """
        store = _store(tmp_db)
        await store.publish(_lesson("a", [1.0, 0.0, 0.0]))
        assert (await store.search([1.0, 0.0, 0.0], limit=1))[0].content == "content of a"

        revised = _lesson("a", [1.0, 0.0, 0.0])
        object.__setattr__(revised, "content", "revised after re-mining")
        await store.publish(revised)

        hit = (await store.search([1.0, 0.0, 0.0], limit=1))[0]
        assert hit.content == "revised after re-mining", (
            "an in-place revision was not seen — COUNT(*) alone cannot detect it"
        )

    async def test_a_deletion_is_visible_immediately(self, tmp_db: DbPool) -> None:
        store = _store(tmp_db)
        await store.publish(_lesson("a", [1.0, 0.0, 0.0]))
        await store.search([1.0, 0.0, 0.0], limit=5)  # warm the cache

        await store.delete("a")

        assert await store.search([1.0, 0.0, 0.0], limit=5) == []

    async def test_a_write_through_ANOTHER_instance_is_seen(
        self, tmp_db: DbPool
    ) -> None:
        """The reason freshness is a DB probe rather than local invalidation.

        StackOwl runs the gateway and the core as separate processes over one
        database. A cache invalidated only on local writes would be correct in a
        single process and silently stale in the real deployment. Two store
        instances over one pool are the closest this test can get to that.
        """
        reader = _store(tmp_db)
        writer = _store(tmp_db)
        await writer.publish(_lesson("a", [1.0, 0.0, 0.0]))
        assert len(await reader.search([1.0, 0.0, 0.0], limit=5)) == 1

        await writer.publish(_lesson("b", [1.0, 0.0, 0.0]))

        assert len(await reader.search([1.0, 0.0, 0.0], limit=5)) == 2, (
            "a write by another instance was invisible — the cache is invalidated "
            "locally rather than from the database, which breaks across processes"
        )

    async def test_both_dimensions_stay_reachable(self, tmp_db: DbPool) -> None:
        """Embedder drift must not make rows unreachable.

        An earlier version kept only the most COMMON dimension, so on an even split
        the survivors were decided by dict order and a legitimate query found nothing.
        """
        store = _store(tmp_db)
        await store.publish(_lesson("two", [1.0, 0.0]))
        await store.publish(_lesson("three", [1.0, 0.0, 0.0]))

        assert [h.lesson_id for h in await store.search([1.0, 0.0], limit=5)] == ["two"]
        assert [h.lesson_id for h in await store.search([1.0, 0.0, 0.0], limit=5)] == ["three"]


class TestSameBatchDuplicates:
    """Ported from test_lessons_lance_dedup.py, which went with the LanceDB adapter.

    THE BUG IT WAS WRITTEN FOR WAS REAL AND IS RECORDED IN THE DATA. LanceDB's
    ``merge_insert`` raised "Ambiguous merge inserts are prohibited" when two SOURCE
    rows in one ``execute()`` matched the same TARGET row, which happens whenever two
    lessons are synthesized for the same skill in a single flush — observed in
    production as ``skill:learned/reks-research-specialist``, and that is the very id
    found FOUR times in the live corpus during the move, residue from before the
    dedup fix landed.

    The vehicle is gone; the invariant is not. A batch carrying the same lesson_id
    twice must neither crash nor leave two rows, and last-wins must match what two
    sequential publish() calls would have done. Here it falls out of the PRIMARY KEY
    plus an upsert rather than from every writer remembering to dedupe first —
    which is why the same bug cannot recur in this store.
    """

    async def test_a_duplicate_id_in_one_batch_does_not_crash(
        self, tmp_db: DbPool
    ) -> None:
        store = _store(tmp_db)
        dup = "skill:learned/reks-research-specialist"

        n = await store.publish_many([
            _lesson(dup, [1.0, 0.0, 0.0], source_type="skill"),
            _lesson(dup, [0.0, 1.0, 0.0], source_type="skill"),
            _lesson("other", [0.0, 0.0, 1.0], source_type="skill"),
        ])

        assert n == 3, "every row is offered to the write; the key resolves the clash"

    async def test_a_duplicate_id_in_one_batch_leaves_exactly_one_row(
        self, tmp_db: DbPool
    ) -> None:
        store = _store(tmp_db)
        dup = "skill:learned/reks-research-specialist"
        await store.publish_many([
            _lesson(dup, [1.0, 0.0, 0.0], source_type="skill"),
            _lesson(dup, [0.0, 1.0, 0.0], source_type="skill"),
        ])

        rows = await tmp_db.fetch_all(
            "SELECT COUNT(*) AS n FROM lessons WHERE lesson_id = ?", (dup,)
        )
        assert int(rows[0]["n"]) == 1, (
            "two rows for one lesson_id — the skill would be double-weighted in "
            "recall, which is exactly what the live corpus had four copies of"
        )

    async def test_last_wins_matching_sequential_publishes(
        self, tmp_db: DbPool
    ) -> None:
        store = _store(tmp_db)
        dup = "skill:learned/reks-research-specialist"
        first = _lesson(dup, [1.0, 0.0, 0.0], source_type="skill")
        second = _lesson(dup, [0.0, 1.0, 0.0], source_type="skill")
        object.__setattr__(second, "content", "the later one")

        await store.publish_many([first, second])

        hits = await store.search([0.0, 1.0, 0.0], limit=1)
        assert hits[0].content == "the later one", hits


class TestReembed:
    """ESC-5 — `db reindex-memory` re-embeds LESSONS now.

    Bakir chose REPOINT over remove: the command stays user-facing, and what it
    reindexes changes. It used to re-embed committed_facts into LanceDB, which was
    dead on both ends — 0 rows on one side, a store being deleted on the other.

    What it is FOR: an embedding-model change makes every stored vector
    incomparable with new queries. The store degrades honestly in that state
    (a query only sees rows of its own dimension), but degrading is not curing.
    This is the cure, and it has to be a real one — hence the assertions on the
    stamped model, not just on the count.
    """

    async def test_it_rewrites_every_vector_and_stamps_the_model(
        self, tmp_db: DbPool
    ) -> None:
        store = _store(tmp_db)
        await store.publish_many([_lesson(f"l{i}", [1.0, 0.0, 0.0]) for i in range(3)])

        async def _embed(texts: list[str]) -> list[list[float]]:
            return [[0.0, 1.0, 0.0] for _ in texts]

        n = await store.reembed_all(_embed, model="new-model-v2")

        assert n == 3
        rows = await tmp_db.fetch_all(
            "SELECT DISTINCT embedding_model FROM lessons", ()
        )
        assert [r["embedding_model"] for r in rows] == ["new-model-v2"]
        # The new vector is what recall now ranks against.
        hits = await store.search([0.0, 1.0, 0.0], limit=3)
        assert all(h.similarity == pytest.approx(1.0) for h in hits), hits

    async def test_it_cures_a_dimension_change(self, tmp_db: DbPool) -> None:
        """The case that actually matters: a new embedder with a new width.

        Before the reindex the old rows are unreachable from a new-width query —
        correctly, since comparing across widths is nonsense. After it, they are
        reachable again. That round trip is the whole point of keeping a command.
        """
        store = _store(tmp_db)
        await store.publish(_lesson("old", [1.0, 0.0, 0.0]))
        assert await store.search([1.0, 0.0, 0.0, 0.0], limit=5) == []

        async def _embed(texts: list[str]) -> list[list[float]]:
            return [[1.0, 0.0, 0.0, 0.0] for _ in texts]

        await store.reembed_all(_embed, model="wider-v3")

        hits = await store.search([1.0, 0.0, 0.0, 0.0], limit=5)
        assert [h.lesson_id for h in hits] == ["old"]

    async def test_an_empty_corpus_reindexes_to_zero_without_erroring(
        self, tmp_db: DbPool
    ) -> None:
        async def _embed(texts: list[str]) -> list[list[float]]:
            raise AssertionError("must not be called for an empty corpus")

        assert await _store(tmp_db).reembed_all(_embed, model="x") == 0

    async def test_content_is_preserved(self, tmp_db: DbPool) -> None:
        """A reindex must change the VECTOR and nothing else — it is a re-embed,
        not a rewrite. Losing content here would be silent: recall would still
        return the row, just with the wrong text."""
        store = _store(tmp_db)
        await store.publish(_lesson("a", [1.0, 0.0, 0.0], source_type="skill"))

        async def _embed(texts: list[str]) -> list[list[float]]:
            return [[0.0, 1.0, 0.0] for _ in texts]

        await store.reembed_all(_embed, model="m")

        hit = (await store.search([0.0, 1.0, 0.0], limit=1))[0]
        assert hit.content == "content of a"
        assert hit.source_type == "skill"
        assert hit.metadata == {"quality": 0.5}
