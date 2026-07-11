"""Tests for SkillIndexStore.index_by_source_name — batch boot-assembly index (LAT.1).

Mirrors list_enabled()'s shape (one owner-scoped query) but keys by
(source, name) instead of collapsing duplicates, since assembly.py's three
back-fill passes must distinguish a builtin and a learned skill that share a
name.
"""
from pathlib import Path

import pytest

from stackowl.skills.loader import LoadedSkill
from stackowl.skills.manifest import SkillManifest
from stackowl.skills.store import SkillIndexStore


def _loaded(name: str, source: str = "user") -> LoadedSkill:
    return LoadedSkill(
        manifest=SkillManifest(name=name, description="d", source=source),
        path=Path("/tmp/x"), body="b", tools_registered=0, owls_registered=0, tool_names=(),
    )


@pytest.mark.asyncio
async def test_returns_all_rows_keyed_by_source_name_tuple(tmp_db):
    store = SkillIndexStore(tmp_db)
    await store.upsert(_loaded("alpha", source="user"))
    await store.upsert(_loaded("dup", source="builtin"))
    await store.upsert(_loaded("dup", source="user"))
    index = await store.index_by_source_name()
    assert set(index.keys()) == {("user", "alpha"), ("builtin", "dup"), ("user", "dup")}
    # A builtin and a learned/user skill sharing a name are both present —
    # get_many_by_name would collapse these to one; this must not.
    assert index[("builtin", "dup")].source == "builtin"
    assert index[("user", "dup")].source == "user"


@pytest.mark.asyncio
async def test_empty_store_returns_empty_dict(tmp_db):
    store = SkillIndexStore(tmp_db)
    assert await store.index_by_source_name() == {}


@pytest.mark.asyncio
async def test_tenancy_isolation(tmp_db):
    a = SkillIndexStore(tmp_db, owner_id="owner-a")
    b = SkillIndexStore(tmp_db, owner_id="owner-b")
    await a.upsert(_loaded("secret"))
    assert await b.index_by_source_name() == {}


@pytest.mark.asyncio
async def test_one_query_regardless_of_row_count(tmp_db):
    store = SkillIndexStore(tmp_db)
    for i in range(50):
        await store.upsert(_loaded(f"skill-{i}"))

    calls = 0
    orig_fetch_all = tmp_db.fetch_all

    async def _counting_fetch_all(sql, params=()):
        nonlocal calls
        calls += 1
        return await orig_fetch_all(sql, params)

    tmp_db.fetch_all = _counting_fetch_all  # type: ignore[method-assign]
    try:
        index = await store.index_by_source_name()
    finally:
        tmp_db.fetch_all = orig_fetch_all  # type: ignore[method-assign]

    assert len(index) == 50
    assert calls == 1
