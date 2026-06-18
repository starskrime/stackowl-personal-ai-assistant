"""Tests for SkillIndexStore.get_many_by_name — bare name resolver (skill-injection T7)."""
from pathlib import Path

import pytest

from stackowl.skills.loader import LoadedSkill
from stackowl.skills.manifest import SkillManifest
from stackowl.skills.store import SkillIndexStore


def _loaded(name, source="user"):
    return LoadedSkill(
        manifest=SkillManifest(name=name, description="d", source=source),
        path=Path("/tmp/x"), body="b", tools_registered=0, owls_registered=0, tool_names=(),
    )


@pytest.mark.asyncio
async def test_resolves_names_preserving_request_order(tmp_db):
    store = SkillIndexStore(tmp_db)
    await store.upsert(_loaded("alpha"))
    await store.upsert(_loaded("beta"))
    got = await store.get_many_by_name(("beta", "alpha", "missing"))
    assert [s.name for s in got] == ["beta", "alpha"]   # missing skipped, order preserved


@pytest.mark.asyncio
async def test_source_priority_picks_user_over_builtin(tmp_db):
    store = SkillIndexStore(tmp_db)
    await store.upsert(_loaded("dup", source="builtin"))
    await store.upsert(_loaded("dup", source="user"))
    got = await store.get_many_by_name(("dup",))
    assert len(got) == 1 and got[0].source == "user"


@pytest.mark.asyncio
async def test_empty_input_returns_empty(tmp_db):
    store = SkillIndexStore(tmp_db)
    assert await store.get_many_by_name(()) == []


@pytest.mark.asyncio
async def test_tenancy_isolation(tmp_db):
    a = SkillIndexStore(tmp_db, owner_id="owner-a")
    b = SkillIndexStore(tmp_db, owner_id="owner-b")
    await a.upsert(_loaded("secret"))
    assert [s.name for s in await b.get_many_by_name(("secret",))] == []
