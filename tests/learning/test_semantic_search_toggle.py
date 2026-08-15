"""ESC-7 — `memory.semantic_search_enabled` gates lessons recall.

WHY THE KEY NEEDED A NEW MEANING. It used to gate SqliteMemoryBridge's semantic
path, which went with LanceDB in D08.2: that path ranked vectors and then hydrated
content from committed_facts, 0 rows since migration 0112. So the key was read by
nothing. Removing it was the obvious tidy-up and the wrong one — MemorySettings is
`extra="forbid"`, so deleting a key any deployment HAS set turns a harmless no-op
into a hard boot failure. Bakir chose to repoint it instead, at the one place
embeddings still rank anything: the lessons corpus.

IT GATES SEARCH, NOT PUBLISH, and that asymmetry is deliberate. Gating writes too
would mean lessons written while the flag was off carried no vector, so turning it
back ON would silently return an incomplete corpus until someone ran a reindex.
Gating reads alone makes the switch instantly reversible in both directions, and
the write cost of embedding a lesson is paid once per lesson rather than per turn.

OFF MUST BE LOUD. "No lessons matched" and "lessons recall is switched off" look
identical to a caller — both are an empty list — and this programme has been
bitten repeatedly by exactly that shape. So the disabled path says so at INFO,
once it has something to say.
"""

from __future__ import annotations

import pytest

from stackowl.db.pool import DbPool
from stackowl.learning.lesson import Lesson
from stackowl.learning.lessons_index import LessonsIndex
from stackowl.learning.lessons_store import SqliteLessonsStore

pytestmark = pytest.mark.asyncio


class _StubProvider:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0, 0.0] for _ in texts]


class _StubRegistry:
    active_model = "stub-v1"

    def get(self) -> _StubProvider:
        return _StubProvider()


async def _seed(db: DbPool) -> SqliteLessonsStore:
    store = SqliteLessonsStore(db)
    await store.publish(
        Lesson(
            lesson_id="reflection:a",
            source_type="reflection",
            source_ref="a",
            content="deploys are slower on fridays",
            embedding=[1.0, 0.0, 0.0],
        )
    )
    return store


async def test_enabled_is_the_default_and_returns_hits(tmp_db: DbPool) -> None:
    """The default must stay ON — the flag defaults True in MemorySettings, and a
    silently-off recall would switch off the reflect->recall arc."""
    index = LessonsIndex(await _seed(tmp_db), embedding_registry=_StubRegistry())

    assert [h.lesson_id for h in await index.search("anything")] == ["reflection:a"]


async def test_disabled_returns_no_hits(tmp_db: DbPool) -> None:
    index = LessonsIndex(
        await _seed(tmp_db),
        embedding_registry=_StubRegistry(),
        semantic_search_enabled=False,
    )

    assert await index.search("anything") == []


async def test_disabled_does_not_even_embed(tmp_db: DbPool) -> None:
    """Switching recall off must stop the WORK, not just discard the result.

    Embedding the query and then throwing the hits away would keep paying the
    cost the operator turned the flag off to avoid.
    """
    calls: list[list[str]] = []

    class _CountingProvider(_StubProvider):
        async def embed(self, texts: list[str]) -> list[list[float]]:
            calls.append(texts)
            return await super().embed(texts)

    class _CountingRegistry(_StubRegistry):
        def get(self) -> _StubProvider:
            return _CountingProvider()

    index = LessonsIndex(
        await _seed(tmp_db),
        embedding_registry=_CountingRegistry(),
        semantic_search_enabled=False,
    )

    await index.search("anything")

    assert calls == [], f"the query was embedded despite recall being off: {calls!r}"


async def test_publish_STILL_writes_while_recall_is_off(tmp_db: DbPool) -> None:
    """The asymmetry, pinned.

    If the flag gated writes too, lessons written while off would carry no
    vector, and turning it back on would return a quietly incomplete corpus
    until someone reindexed. Writing through keeps the switch reversible.
    """
    store = await _seed(tmp_db)
    index = LessonsIndex(
        store, embedding_registry=_StubRegistry(), semantic_search_enabled=False
    )

    from stackowl.learning.lessons_index import LessonDraft

    ok = await index.publish(
        LessonDraft(
            source_type="reflection",
            source_ref="written-while-off",
            content="a lesson learned during the outage",
            metadata={},
        )
    )

    assert ok is True, "publish was refused while recall was off"
    assert await store.count() == 2

    # And it is immediately recallable once the flag comes back on — no reindex.
    back_on = LessonsIndex(store, embedding_registry=_StubRegistry())
    assert len(await back_on.search("anything", limit=5)) == 2
