"""LAT.4 — SkillIndexStore.set_embeddings_batch() replaces the per-skill
set_embedding() autocommit loop SkillsAssembly._embed_missing runs at boot
(pool.py:27-38's documented ~24-40s / ~300-row catalog-scan starvation case)
with bounded chunked transactions.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stackowl.db.pool import DbPool
from stackowl.skills.loader import LoadedSkill
from stackowl.skills.manifest import SkillManifest
from stackowl.skills.store import _EMBED_CHUNK_SIZE, SkillIndexStore

pytestmark = pytest.mark.asyncio


def _loaded(name: str) -> LoadedSkill:
    return LoadedSkill(
        manifest=SkillManifest(name=name, description="d", source="user"),
        path=Path("/tmp/x"), body="body",
        tools_registered=0, owls_registered=0, tool_names=(),
    )


async def _seed_skills(store: SkillIndexStore, n: int) -> list[int]:
    ids = []
    for i in range(n):
        skill_id = await store.upsert(_loaded(f"skill-{i}"))
        ids.append(skill_id)
    return ids


async def test_batch_larger_than_chunk_size_commits_in_multiple_bounded_chunks(
    tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = SkillIndexStore(tmp_db)
    n = _EMBED_CHUNK_SIZE * 2 + 5
    ids = await _seed_skills(store, n)

    tx_calls = 0
    orig_transaction = tmp_db.transaction

    def counting_transaction():  # type: ignore[no-untyped-def]
        nonlocal tx_calls
        tx_calls += 1
        return orig_transaction()

    tmp_db.transaction = counting_transaction  # type: ignore[method-assign]

    items = [(skill_id, [0.1, 0.2, 0.3], "stub-embed-v1") for skill_id in ids]
    await store.set_embeddings_batch(items)

    # ceil(n / _EMBED_CHUNK_SIZE) == 3
    assert tx_calls == 3
    assert 50 <= _EMBED_CHUNK_SIZE <= 100

    for skill_id in ids:
        rows = await tmp_db.fetch_all(
            "SELECT embedding, embedding_model FROM skills WHERE skill_id = ?",
            (skill_id,),
        )
        assert rows[0]["embedding"] is not None
        assert rows[0]["embedding_model"] == "stub-embed-v1"


async def test_empty_batch_is_a_noop(tmp_db: DbPool) -> None:
    store = SkillIndexStore(tmp_db)
    await store.set_embeddings_batch([])  # must not raise / must not open a transaction
