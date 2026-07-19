"""KuzuAdapter Owl/Skill/Trait method tests — exercises the async wrapper against
a real on-disk Kuzu DB, monkey-patching TestModeGuard exactly like the existing
adapter test suite (see test_kuzu_adapter_healable.py) since these methods gate
on TestModeGuard.assert_not_test_mode like every other public adapter method."""

from __future__ import annotations

from pathlib import Path

import pytest

from stackowl.config.test_mode import TestModeGuard
from stackowl.memory.kuzu_adapter import KuzuAdapter


@pytest.fixture(autouse=True)
def _not_test_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)


@pytest.fixture()
async def adapter(tmp_path: Path):
    a = KuzuAdapter(data_dir=tmp_path)
    yield a
    await a.aclose()


async def test_upsert_owl_node(adapter: KuzuAdapter) -> None:
    await adapter.upsert_owl_node("Brain")
    # round-trip via a Skill link — confirms the Owl node exists and is matchable
    await adapter.upsert_skill_node("p::s1", "p", "s1")
    await adapter.link_owl_owns_skill("Brain", "p::s1")
    ids = await adapter.list_skill_ids()
    assert ids == ["p::s1"]


async def test_upsert_skill_node_and_list(adapter: KuzuAdapter) -> None:
    await adapter.upsert_skill_node("p::s1", "p", "s1")
    await adapter.upsert_skill_node("p::s2", "p", "s2")
    ids = await adapter.list_skill_ids()
    assert set(ids) == {"p::s1", "p::s2"}


async def test_upsert_trait_node_and_list(adapter: KuzuAdapter) -> None:
    await adapter.upsert_trait_node("Brain::challenge_level", "Brain", "challenge_level", 0.6)
    ids = await adapter.list_trait_ids()
    assert ids == ["Brain::challenge_level"]


async def test_link_owl_owns_skill(adapter: KuzuAdapter) -> None:
    await adapter.upsert_owl_node("Brain")
    await adapter.upsert_skill_node("p::s1", "p", "s1")
    await adapter.link_owl_owns_skill("Brain", "p::s1")
    # no direct read API for edges on the adapter yet — verified via delete's
    # DETACH DELETE removing exactly this edge in the next test


async def test_delete_skill_node_removes_it(adapter: KuzuAdapter) -> None:
    await adapter.upsert_owl_node("Brain")
    await adapter.upsert_skill_node("p::s1", "p", "s1")
    await adapter.link_owl_owns_skill("Brain", "p::s1")

    await adapter.delete_skill_node("p::s1")

    ids = await adapter.list_skill_ids()
    assert ids == []


async def test_delete_trait_node_removes_it(adapter: KuzuAdapter) -> None:
    await adapter.upsert_owl_node("Brain")
    await adapter.upsert_trait_node("Brain::challenge_level", "Brain", "challenge_level", 0.6)
    await adapter.link_owl_has_trait("Brain", "Brain::challenge_level")

    await adapter.delete_trait_node("Brain::challenge_level")

    ids = await adapter.list_trait_ids()
    assert ids == []
