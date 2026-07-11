"""SkillIndexStore.hybrid_recall (Story LAT.2, Task 3) — BM25 + cosine, RRF-fused.

Two regression guards proving each side of the fusion does real work:
  - a skill with NO embedding is reachable ONLY via the keyword (BM25) pass
    (semantic_recall structurally excludes embedding-less rows)
  - a skill with NO shared keyword vocabulary is reachable ONLY via the
    embedding (cosine) pass (FTS5 MATCH structurally excludes it)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stackowl.db.pool import DbPool
from stackowl.skills.loader import LoadedSkill
from stackowl.skills.manifest import SkillManifest
from stackowl.skills.store import SkillIndexStore


def _loaded(name, description, when_to_use="w"):
    return LoadedSkill(
        manifest=SkillManifest(name=name, description=description, when_to_use=when_to_use, source="user"),
        path=Path("/tmp/x"), body="body", tools_registered=0,
        owls_registered=0, tool_names=(),
    )


@pytest.mark.asyncio
async def test_hybrid_recall_surfaces_keyword_only_match_with_no_embedding(tmp_db: DbPool):
    """A skill with an exact rare-term match in its description, but NO
    embedding row, is unreachable via semantic_recall alone (it filters
    embedding IS NOT NULL) yet must still surface via hybrid_recall's
    keyword pass."""
    store = SkillIndexStore(tmp_db)
    await store.upsert(_loaded(
        "frobnicator-tool", "Uses the frobnicator protocol to talk to the device.",
    ))
    # A second, embedded skill whose vector is orthogonal to the query — proves
    # the keyword-only skill isn't riding along via a coincidental cosine hit.
    other_id = await store.upsert(_loaded("unrelated-tool", "Does something else entirely."))
    await store.set_embedding(other_id, [0.0, 1.0], "stub-v1")

    query_embedding = [1.0, 0.0]  # orthogonal to the only embedded skill
    # semantic_recall alone cannot find the keyword-only skill (no embedding).
    semantic_only = await store.semantic_recall(query_embedding, limit=10)
    assert "frobnicator-tool" not in {sk.name for sk, _ in semantic_only}

    hits = await store.hybrid_recall("frobnicator", query_embedding, limit=10)
    assert "frobnicator-tool" in {sk.name for sk, _score in hits}


@pytest.mark.asyncio
async def test_hybrid_recall_surfaces_semantic_only_match_with_no_shared_keyword(tmp_db: DbPool):
    """A skill with NO keyword overlap with the query text, but a close
    embedding, is unreachable via the FTS5 keyword pass yet must still
    surface via hybrid_recall's cosine pass."""
    store = SkillIndexStore(tmp_db)
    skill_id = await store.upsert(_loaded(
        "condenser", "Handles zzzqux zzzwibble zzzflorp document processing.",
    ))
    await store.set_embedding(skill_id, [1.0, 0.0, 0.0], "stub-v1")

    query_text = "yyyalpha yyybeta yyygamma"  # zero token overlap with the skill
    query_embedding = [0.99, 0.01, 0.0]  # numerically close to the skill's vector

    # The FTS5 keyword pass alone cannot find it (no shared token).
    keyword_only = await store._fts_search(query_text, limit=10)
    assert "condenser" not in {sk.name for sk in keyword_only}

    hits = await store.hybrid_recall(query_text, query_embedding, limit=10)
    assert "condenser" in {sk.name for sk, _score in hits}


@pytest.mark.asyncio
async def test_hybrid_recall_respects_limit(tmp_db: DbPool):
    store = SkillIndexStore(tmp_db)
    for i in range(5):
        sid = await store.upsert(_loaded(f"skill-{i}", f"skill number {i} about widgets"))
        await store.set_embedding(sid, [float(i), 1.0], "stub-v1")

    hits = await store.hybrid_recall("widgets", [1.0, 1.0], limit=2)
    assert len(hits) == 2
