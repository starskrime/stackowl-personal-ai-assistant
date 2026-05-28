"""Tests for PreferenceStore — get/set/list/delete + per-owner isolation."""

from __future__ import annotations

import pytest

from stackowl.db.pool import DbPool
from stackowl.memory.preferences import PreferenceStore

pytestmark = pytest.mark.asyncio


async def test_set_and_get_round_trip(tmp_db: DbPool) -> None:
    store = PreferenceStore(db=tmp_db)
    await store.set("local", "provider_tier", "powerful")
    assert await store.get("local", "provider_tier") == "powerful"


async def test_get_returns_none_for_missing_key(tmp_db: DbPool) -> None:
    store = PreferenceStore(db=tmp_db)
    assert await store.get("local", "unknown") is None


async def test_set_upserts_on_duplicate_key(tmp_db: DbPool) -> None:
    store = PreferenceStore(db=tmp_db)
    await store.set("local", "provider_tier", "fast")
    await store.set("local", "provider_tier", "powerful")
    assert await store.get("local", "provider_tier") == "powerful"
    # No duplicates leak.
    prefs = await store.list_for_owner("local")
    assert prefs == {"provider_tier": "powerful"}


async def test_owner_isolation(tmp_db: DbPool) -> None:
    store = PreferenceStore(db=tmp_db)
    await store.set("user-a", "provider_tier", "fast")
    await store.set("user-b", "provider_tier", "powerful")
    assert await store.get("user-a", "provider_tier") == "fast"
    assert await store.get("user-b", "provider_tier") == "powerful"
    assert await store.list_for_owner("user-a") == {"provider_tier": "fast"}
    assert await store.list_for_owner("user-b") == {"provider_tier": "powerful"}
    # User-c is empty.
    assert await store.list_for_owner("user-c") == {}


async def test_delete_removes_only_the_targeted_pref(tmp_db: DbPool) -> None:
    store = PreferenceStore(db=tmp_db)
    await store.set("local", "provider_tier", "fast")
    await store.set("local", "response_style", "markdown bullets")
    await store.delete("local", "provider_tier")
    assert await store.get("local", "provider_tier") is None
    assert await store.get("local", "response_style") == "markdown bullets"


async def test_list_for_owner_returns_all_keys(tmp_db: DbPool) -> None:
    store = PreferenceStore(db=tmp_db)
    await store.set("local", "k1", "v1")
    await store.set("local", "k2", "v2")
    await store.set("local", "k3", "v3")
    prefs = await store.list_for_owner("local")
    assert prefs == {"k1": "v1", "k2": "v2", "k3": "v3"}
