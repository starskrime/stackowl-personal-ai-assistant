"""SqliteLessonsStore — the lessons corpus in SQLite, ranked by a numpy scan.

WHY THIS REPLACED A VECTOR DATABASE. Bakir, 2026-08-14: "I do not want lancedb at
all because it is heavy and does not support all platforms." Measured rather than
taken on faith: `lancedb` is 100MB and requires `pyarrow` at 136MB — 236MB of
dependency, against `numpy` at 30MB which was already a direct dependency. On the
Jetson-class box that is the only dev machine here, the wheel-availability problem
is not hypothetical.

WHY BRUTE FORCE IS THE RIGHT ANSWER AND NOT A COMPROMISE. The live corpus is 3,679
lessons at 384 dimensions — 5.4MB as float32. An ANN index buys nothing at this
size, and it is APPROXIMATE where this is EXACT, so recall quality goes up.

MEASURED, because the first version of this docstring guessed and was wrong. I
claimed "well under a millisecond" for the ranking; the honest numbers on the live
corpus are ~7ms for the numpy work (squared distances + argsort) and 35-70ms to
LOAD the 3,679 rows out of SQLite, depending on page-cache warmth. The load, not
the arithmetic, is the cost — five to ten times over. Two things were tried and
rejected on measurement rather than taste: selecting only the vector columns barely
helps (35.6ms vs 39.1ms warm, because the cost is constructing rows at all), and a
two-phase rank-then-hydrate is WORSE at 59ms because it pays for two queries.

So the corpus is CACHED in-process. Final measured numbers on the live corpus:
69.5ms for the first search in a process (which loads it) and 7.9-12.1ms for every
search after — the arithmetic cost, which is where it should be.

The freshness probe needed its own measurement too, and the first version of it was
a guard more expensive than the thing it guarded: `MAX(updated_at)` was scanning
every row at 11.6ms, against 0.21ms for `COUNT(*)`. Migration 0117 indexes it and
the probe is now 0.56ms. Dropping `updated_at` for the already-fast `MAX(rowid)`
would have been the wrong fix — rowid does not move on an UPDATE, so it cannot see
a re-mined lesson.

THE CONTRACT THAT HAD TO BE PRESERVED IS NUMERIC. ``heuristic_ranking`` scores hits
as ``similarity + c * quality * sqrt(ln N / evidence)``, so the SCALE of similarity
is coupled to a tuned constant — change the metric and ranking silently
re-balances while every test still passes. So the old metric was measured on the
live LanceDB table instead of assumed: ``_distance`` was SQUARED L2 (0.510582 for a
pair whose L2 is 0.714550), and the adapter returned ``1 / (1 + distance)``. This
computes the same number. ``tests/learning/test_lessons_sqlite_store.py::
test_similarity_matches_the_lancedb_formula_exactly`` pins it.

SQUARED DISTANCE IS COMPUTED IN FULL, as ``||q||^2 + ||e||^2 - 2 q.e``, rather than
via the cosine shortcut. The live vectors happen to be unit-norm, which would make
the two identical — but that is a property of today's embedder, not a guarantee of
the schema, and a future non-normalised embedder would silently skew every score.

DEGRADING HONESTLY. The cached corpus is GROUPED BY embedding dimension, and a
query only ever sees the group matching its own — never truncated, never compared
across, never a crash on the turn path. That is the F062 lesson from the fact
store: when the active embedder changes, old vectors are incomparable, and
comparing them anyway returns confident nonsense. An earlier version of this reduced
the corpus to its most COMMON dimension and dropped the rest, which a test caught:
on an even split the winner was whichever the dict yielded first, so a query could
find nothing while perfectly comparable rows sat in the corpus.
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING

import numpy as np

from stackowl.infra.observability import log
from stackowl.learning.lesson import Lesson, LessonHit
from stackowl.memory.sqlite_helpers import pack_embedding, unpack_embedding

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.db.pool import DbPool

_UPSERT_SQL = """
INSERT INTO lessons
    (lesson_id, source_type, source_ref, content, embedding, embedding_model,
     metadata, updated_at)
VALUES (?, ?, ?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ','now'))
ON CONFLICT(lesson_id) DO UPDATE SET
    source_type     = excluded.source_type,
    source_ref      = excluded.source_ref,
    content         = excluded.content,
    embedding       = excluded.embedding,
    embedding_model = excluded.embedding_model,
    metadata        = excluded.metadata,
    updated_at      = excluded.updated_at
"""

#: The cache-validity probe. One aggregate instead of thousands of row
#: constructions. COUNT alone would miss an in-place revision — lesson_id is
#: "<source>:<source_ref>", so a re-mined lesson upserts the same row.
_STAMP_SQL = "SELECT COUNT(*) AS n, COALESCE(MAX(updated_at), '') AS mx FROM lessons"

_SELECT_ALL_SQL = (
    "SELECT lesson_id, source_type, source_ref, content, embedding, metadata "
    "FROM lessons"
)
_SELECT_BY_SOURCE_SQL = _SELECT_ALL_SQL + " WHERE source_type = ?"


class SqliteLessonsStore:
    """Publish/search/delete over the ``lessons`` table.

    Same surface as the LanceDB adapter it replaces — ``publish``,
    ``publish_many``, ``search``, ``delete`` — so ``LessonsIndex`` did not have to
    change shape to adopt it.
    """

    def __init__(self, db: DbPool, *, embedding_model: str = "") -> None:
        # 1. ENTRY / 4. EXIT — nothing to build; the table is the state.
        self._db = db
        self._embedding_model = embedding_model
        # In-process cache of the ranked corpus. MEASURED justification: the numpy
        # work is ~7ms while loading 3,679 rows is 35-70ms, so the load — not the
        # arithmetic — is what a per-turn search would otherwise pay every time.
        self._cache: dict[int, tuple[list[dict[str, object]], np.ndarray]] | None = None
        self._cache_stamp: tuple[int, str] | None = None
        log.memory.debug("[learning] lessons_store.init: ready")

    async def publish(self, lesson: Lesson) -> None:
        """Insert or update one lesson.

        REFUSES a lesson with no embedding. A row with no vector can never be
        returned by search, so storing one is a write with no reader — and it
        would be invisible rather than loud.
        """
        # 1. ENTRY
        t0 = time.monotonic()
        log.memory.debug(
            "[learning] lessons_store.publish: entry",
            extra={"_fields": {"lesson_id": lesson.lesson_id}},
        )
        if not lesson.embedding:
            raise ValueError(
                f"lesson {lesson.lesson_id!r} has no embedding — it could never be "
                "recalled, so it is refused rather than silently stored"
            )
        # 3. STEP
        await self._db.execute(_UPSERT_SQL, self._row(lesson))
        # 4. EXIT
        log.memory.info(
            "[learning] lessons_store.publish: stored",
            extra={"_fields": {
                "lesson_id": lesson.lesson_id,
                "source_type": lesson.source_type,
                "duration_ms": (time.monotonic() - t0) * 1000,
            }},
        )

    async def publish_many(self, lessons: list[Lesson]) -> int:
        """Insert or update many lessons. Returns how many were written.

        Lessons with no embedding are SKIPPED and counted in the log rather than
        aborting the batch: a miner producing one bad row should not cost the
        other forty-nine.
        """
        # 1. ENTRY
        t0 = time.monotonic()
        log.memory.debug(
            "[learning] lessons_store.publish_many: entry",
            extra={"_fields": {"n": len(lessons)}},
        )
        rows = [self._row(x) for x in lessons if x.embedding]
        skipped = len(lessons) - len(rows)
        # 2. DECISION
        if not rows:
            log.memory.info(
                "[learning] lessons_store.publish_many: exit — nothing to write",
                extra={"_fields": {"skipped_no_embedding": skipped}},
            )
            return 0
        # 3. STEP
        async with self._db.transaction() as tx:
            for row in rows:
                await tx.execute(_UPSERT_SQL, row)
        # 4. EXIT
        log.memory.info(
            "[learning] lessons_store.publish_many: stored",
            extra={"_fields": {
                "written": len(rows),
                "skipped_no_embedding": skipped,
                "duration_ms": (time.monotonic() - t0) * 1000,
            }},
        )
        return len(rows)

    async def search(
        self,
        query_embedding: list[float],
        *,
        limit: int = 5,
        source_filter: str | None = None,
    ) -> list[LessonHit]:
        """Rank the corpus against ``query_embedding`` and return the top ``limit``.

        ``source_filter`` is bound as a PARAMETER, not interpolated — the LanceDB
        adapter hand-escaped quotes into a ``where`` string, and a filter that
        silently matched everything would leak other tiers into a scoped search.
        """
        # 1. ENTRY
        t0 = time.monotonic()
        log.memory.debug(
            "[learning] lessons_store.search: entry",
            extra={"_fields": {
                "dim": len(query_embedding),
                "limit": limit,
                "source_filter": source_filter,
            }},
        )
        # 2. DECISION — an embedder that failed hands back []; searching on it must
        # not be a crash on the turn path.
        if not query_embedding:
            return []
        query = np.asarray(query_embedding, dtype=np.float32)
        dim = int(query.shape[0])
        by_dim = await self._corpus()
        # 2. DECISION — F062: rows written under a different embedder are
        # incomparable. A query sees only its OWN dimension; comparing across would
        # return confident nonsense, and crashing would cost the turn its recall.
        group = by_dim.get(dim)
        if group is None:
            if by_dim:
                log.memory.warning(
                    "[learning] lessons_store.search: no rows share the query's "
                    "embedding dimension — embedder drift, returning no hits "
                    "rather than wrong ones",
                    extra={"_fields": {
                        "query_dim": dim,
                        "corpus_dims": {d: len(r) for d, (r, _m) in by_dim.items()},
                    }},
                )
            else:
                log.memory.debug("[learning] lessons_store.search: exit — empty corpus")
            return []
        rows, matrix = group

        # 3. STEP — the only remaining predicate is the caller's tier filter.
        if source_filter is not None:
            idx = np.flatnonzero(
                np.fromiter(
                    (r["source_type"] == source_filter for r in rows),
                    dtype=bool,
                    count=len(rows),
                )
            )
            if idx.size == 0:
                return []
            usable = [rows[i] for i in idx]
            vectors = matrix[idx]
        else:
            usable, vectors = rows, matrix
        # Squared L2 in full: ||q||^2 + ||e||^2 - 2 q.e. The identity with cosine
        # holds only for unit-norm vectors, which is a property of today's embedder
        # rather than of this schema.
        distances = (
            float(query @ query) + np.einsum("ij,ij->i", vectors, vectors)
            - 2.0 * (vectors @ query)
        )
        # Floating point can push an exact match a hair below zero.
        np.maximum(distances, 0.0, out=distances)
        top = np.argsort(distances)[:limit]

        hits = [
            LessonHit(
                lesson_id=usable[i]["lesson_id"],
                source_type=usable[i]["source_type"],
                source_ref=usable[i]["source_ref"],
                content=usable[i]["content"] or "",
                similarity=1.0 / (1.0 + float(distances[i])),
                metadata=self._metadata(usable[i]),
            )
            for i in top
        ]
        # 4. EXIT
        log.memory.debug(
            "[learning] lessons_store.search: exit",
            extra={"_fields": {
                "n_hits": len(hits),
                "scanned": len(usable),
                # Rows the CORPUS dropped for a minority embedding dimension —
                # decided at load, not per query, since a matrix must be
                # rectangular. Reported here so a drifted corpus is visible on the
                # search line rather than only on the load line.
                "corpus_dim": dim,
                "duration_ms": (time.monotonic() - t0) * 1000,
            }},
        )
        return hits

    async def delete(self, lesson_id: str) -> None:
        """Remove a lesson by id (used when the underlying skill is deprecated)."""
        # 1. ENTRY
        log.memory.debug(
            "[learning] lessons_store.delete: entry",
            extra={"_fields": {"lesson_id": lesson_id}},
        )
        await self._db.execute("DELETE FROM lessons WHERE lesson_id = ?", (lesson_id,))
        # 4. EXIT
        log.memory.info(
            "[learning] lessons_store.delete: removed",
            extra={"_fields": {"lesson_id": lesson_id}},
        )

    async def count(self) -> int:
        """How many lessons are stored. Used by the reindex command and health."""
        rows = await self._db.fetch_all("SELECT COUNT(*) AS n FROM lessons", ())
        return int(rows[0]["n"])

    async def _corpus(self) -> dict[int, tuple[list[dict[str, object]], np.ndarray]]:
        """The corpus GROUPED BY embedding dimension, cached in-process.

        CACHED because loading is the expensive half: ~7ms of numpy against 35-70ms
        to build 3,679 rows out of SQLite. Freshness is decided by ONE mechanism —
        a `COUNT(*), MAX(updated_at)` probe — rather than also invalidating on local
        writes. Two mechanisms could disagree, and the probe is the only one that
        works across the gateway/core process split, where a write in one process
        cannot invalidate a cache in the other. MAX(updated_at) rather than COUNT
        alone because lesson_id is "<source>:<source_ref>", so a re-mined lesson
        upserts the SAME row and leaves the count unchanged.

        GROUPED BY DIMENSION rather than reduced to one matrix. A matrix must be
        rectangular, so mixed dimensions have to be separated somehow — and an
        earlier version picked the most common dimension and dropped the rest.
        That was wrong in a way tests caught: with an even split the "winner" was
        whichever the dict happened to yield first, so a query would silently find
        nothing while perfectly comparable rows sat in the corpus. Grouping keeps
        every row reachable by a query of its own dimension, and makes embedder
        drift a thing the store reports rather than resolves by guessing.
        """
        stamp_row = (await self._db.fetch_all(_STAMP_SQL, ()))[0]
        stamp = (int(stamp_row["n"]), str(stamp_row["mx"]))
        if self._cache is not None and self._cache_stamp == stamp:
            return self._cache

        t0 = time.monotonic()
        raw = await self._db.fetch_all(_SELECT_ALL_SQL, ())
        grouped: dict[int, tuple[list[dict[str, object]], list[np.ndarray]]] = {}
        for row in raw:
            vec = np.frombuffer(row["embedding"], dtype="<f4")
            rows_for_dim, vecs_for_dim = grouped.setdefault(vec.shape[0], ([], []))
            rows_for_dim.append(row)
            vecs_for_dim.append(vec)

        cache = {
            dim: (rows_for_dim, np.stack(vecs_for_dim))
            for dim, (rows_for_dim, vecs_for_dim) in grouped.items()
        }
        if len(cache) > 1:
            log.memory.warning(
                "[learning] lessons_store: the corpus holds MORE THAN ONE embedding "
                "dimension — it was written by more than one embedder, and a query "
                "only ever sees the rows matching its own",
                extra={"_fields": {d: len(r) for d, (r, _m) in cache.items()}},
            )
        log.memory.info(
            "[learning] lessons_store: corpus loaded",
            extra={"_fields": {
                "lessons": len(raw),
                "dims": {d: len(r) for d, (r, _m) in cache.items()},
                "load_ms": (time.monotonic() - t0) * 1000,
            }},
        )
        self._cache = cache
        self._cache_stamp = stamp
        return cache

    # ----- internal ---------------------------------------------------------

    def _row(self, lesson: Lesson) -> tuple[object, ...]:
        return (
            lesson.lesson_id,
            lesson.source_type,
            lesson.source_ref,
            lesson.content,
            pack_embedding(list(lesson.embedding)),
            self._embedding_model,
            json.dumps(lesson.metadata or {}),
        )

    @staticmethod
    def _metadata(row: dict[str, object]) -> dict[str, object]:
        """Parse the metadata JSON, treating corruption as empty rather than fatal.

        Ranking reads `quality` and `evidence_count` out of this dict, so a
        corrupt row costs that hit its exploration term — it must not cost the
        turn its recall.
        """
        raw = row.get("metadata") or "{}"
        try:
            parsed = json.loads(str(raw))
        except json.JSONDecodeError as exc:
            log.memory.warning(
                "[learning] lessons_store: corrupt metadata JSON — using empty",
                exc_info=exc,
                extra={"_fields": {"lesson_id": row.get("lesson_id")}},
            )
            return {}
        return parsed if isinstance(parsed, dict) else {}


__all__ = ["SqliteLessonsStore", "unpack_embedding"]
