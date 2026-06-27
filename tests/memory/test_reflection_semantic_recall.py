"""Tests for F-50 — semantic recall over reflections (intent-matched, not recency-only).

`recent_for_owl` returns last-N by created_at. `semantic_for_owl` reuses the
embedding engine to surface reflections matching the *current intent*, with
recency as a tie-breaker. Reflections are positive-only (success-only) — no
directive conflict.
"""

from __future__ import annotations

import pytest

from stackowl.db.pool import DbPool
from stackowl.memory.outcome_store import TaskOutcomeStore
from stackowl.memory.reflection_store import ReflectionStore

pytestmark = pytest.mark.asyncio


class _FakeProvider:
    """Minimal EmbeddingProvider stand-in: returns a fixed vector per text."""

    def __init__(self, mapping: dict[str, list[float]]) -> None:
        self._mapping = mapping

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._mapping[t] for t in texts]

    @property
    def model_name(self) -> str:
        return "fake-v1"


class _FakeRegistry:
    """EmbeddingRegistry stand-in exposing the .get() seam the store uses."""

    def __init__(self, provider: _FakeProvider) -> None:
        self._provider = provider

    def get(self) -> _FakeProvider:
        return self._provider


async def _seed(
    db: DbPool, *, trace_id: str, owl: str, summary: str,
    embedding: list[float] | None,
) -> None:
    out_store = TaskOutcomeStore(db)
    await out_store.record(
        trace_id=trace_id, session_id="s", owl_name=owl, channel="cli",
        success=True, latency_ms=10.0, tool_call_count=1,
        failure_class=None, step_durations={},
        input_text="hi", response_text="ok",
    )
    rstore = ReflectionStore(db)
    await rstore.write(
        trace_id=trace_id, owl_name=owl, summary=summary,
        suggested_strategy="strat", failure_class=None, quality_score=0.9,
        embedding=embedding, embedding_model="fake-v1" if embedding else None,
    )


async def test_semantic_for_owl_ranks_by_similarity(tmp_db: DbPool) -> None:
    """Closest-by-cosine reflection comes first, not the most recent."""
    await _seed(tmp_db, trace_id="a", owl="scout", summary="use caching",
                embedding=[1.0, 0.0, 0.0])
    await _seed(tmp_db, trace_id="b", owl="scout", summary="parallel fan-out",
                embedding=[0.0, 1.0, 0.0])
    await _seed(tmp_db, trace_id="c", owl="scout", summary="retry with backoff",
                embedding=[0.0, 0.0, 1.0])

    # Query vector points mostly at "use caching" (a), then b, then c.
    registry = _FakeRegistry(_FakeProvider({"how do I speed it up": [0.9, 0.1, 0.0]}))
    rstore = ReflectionStore(tmp_db)
    hits = await rstore.semantic_for_owl(
        "scout", "how do I speed it up", registry, limit=3,
    )
    assert [r.summary for r in hits] == [
        "use caching", "parallel fan-out", "retry with backoff",
    ]


async def test_semantic_for_owl_respects_limit(tmp_db: DbPool) -> None:
    await _seed(tmp_db, trace_id="a", owl="scout", summary="aa",
                embedding=[1.0, 0.0])
    await _seed(tmp_db, trace_id="b", owl="scout", summary="bb",
                embedding=[0.0, 1.0])
    registry = _FakeRegistry(_FakeProvider({"q": [1.0, 0.0]}))
    rstore = ReflectionStore(tmp_db)
    hits = await rstore.semantic_for_owl("scout", "q", registry, limit=1)
    assert [r.summary for r in hits] == ["aa"]


async def test_semantic_for_owl_filters_by_owl(tmp_db: DbPool) -> None:
    await _seed(tmp_db, trace_id="a", owl="scout", summary="scout note",
                embedding=[1.0, 0.0])
    await _seed(tmp_db, trace_id="b", owl="librarian", summary="lib note",
                embedding=[1.0, 0.0])
    registry = _FakeRegistry(_FakeProvider({"q": [1.0, 0.0]}))
    rstore = ReflectionStore(tmp_db)
    hits = await rstore.semantic_for_owl("scout", "q", registry, limit=5)
    assert [r.summary for r in hits] == ["scout note"]


async def test_semantic_for_owl_recency_tiebreak(tmp_db: DbPool) -> None:
    """Identical similarity -> newest reflection first (recency tie-break)."""
    await _seed(tmp_db, trace_id="old", owl="scout", summary="old",
                embedding=[1.0, 0.0])
    await _seed(tmp_db, trace_id="new", owl="scout", summary="new",
                embedding=[1.0, 0.0])
    registry = _FakeRegistry(_FakeProvider({"q": [1.0, 0.0]}))
    rstore = ReflectionStore(tmp_db)
    hits = await rstore.semantic_for_owl("scout", "q", registry, limit=2)
    # Both perfectly aligned; the more recently created ("new") wins the tie.
    assert hits[0].summary == "new"


async def test_semantic_for_owl_falls_back_to_recency_without_embeddings(
    tmp_db: DbPool,
) -> None:
    """No embedded candidates -> degrade to recency order (never crash, never empty)."""
    await _seed(tmp_db, trace_id="a", owl="scout", summary="first", embedding=None)
    await _seed(tmp_db, trace_id="b", owl="scout", summary="second", embedding=None)
    registry = _FakeRegistry(_FakeProvider({"q": [1.0, 0.0]}))
    rstore = ReflectionStore(tmp_db)
    hits = await rstore.semantic_for_owl("scout", "q", registry, limit=5)
    # recent_for_owl order: newest-first.
    assert [r.summary for r in hits] == ["second", "first"]


async def test_semantic_for_owl_falls_back_when_embed_raises(tmp_db: DbPool) -> None:
    """Embed failure must degrade to recency, not raise."""
    await _seed(tmp_db, trace_id="a", owl="scout", summary="only",
                embedding=[1.0, 0.0])

    class _BoomProvider:
        async def embed(self, texts: list[str]) -> list[list[float]]:
            raise RuntimeError("model down")

        @property
        def model_name(self) -> str:
            return "boom"

    class _BoomRegistry:
        def get(self) -> _BoomProvider:
            return _BoomProvider()

    rstore = ReflectionStore(tmp_db)
    hits = await rstore.semantic_for_owl("scout", "q", _BoomRegistry(), limit=5)
    assert [r.summary for r in hits] == ["only"]
