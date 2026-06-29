"""Tests for skill_ownership: attach overlay + persist/read + boot hydrate (PA4b)."""

from __future__ import annotations

import pytest

from stackowl.db.pool import DbPool
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.owls.skill_ownership import (
    attach_skill_to_owl,
    hydrate_skill_ownership,
    persist_skill_ownership,
    purge_skill_ownership,
    read_all_skill_ownership,
)


def _reg() -> OwlRegistry:
    r = OwlRegistry.with_default_secretary()
    r.register(
        OwlAgentManifest(name="scout", role="research", system_prompt="P", model_tier="fast")
    )
    return r


# ---------- live overlay ----------------------------------------------------

def test_attach_adds_to_manifest_skills() -> None:
    r = _reg()
    assert attach_skill_to_owl(r, "scout", "web-scrape") is True
    assert "web-scrape" in r.get("scout").skills


def test_attach_is_idempotent() -> None:
    r = _reg()
    assert attach_skill_to_owl(r, "scout", "web-scrape") is True
    assert attach_skill_to_owl(r, "scout", "web-scrape") is False  # already owned
    assert list(r.get("scout").skills).count("web-scrape") == 1


def test_attach_orphan_returns_false() -> None:
    assert attach_skill_to_owl(_reg(), "ghost", "web-scrape") is False


# ---------- durable persist + read -----------------------------------------

@pytest.mark.asyncio
async def test_persist_read_round_trip(tmp_db: DbPool) -> None:
    await persist_skill_ownership(tmp_db, "scout", "web-scrape")
    owned = await read_all_skill_ownership(tmp_db)
    assert owned == {"scout": ["web-scrape"]}


@pytest.mark.asyncio
async def test_persist_is_idempotent(tmp_db: DbPool) -> None:
    await persist_skill_ownership(tmp_db, "scout", "web-scrape")
    await persist_skill_ownership(tmp_db, "scout", "web-scrape")  # no error, no dup row
    owned = await read_all_skill_ownership(tmp_db)
    assert owned["scout"] == ["web-scrape"]


# ---------- boot hydration --------------------------------------------------

@pytest.mark.asyncio
async def test_hydrate_attaches_persisted_row(tmp_db: DbPool) -> None:
    r = _reg()
    await persist_skill_ownership(tmp_db, "scout", "web-scrape")
    assert await hydrate_skill_ownership(r, tmp_db) == 1
    assert "web-scrape" in r.get("scout").skills


@pytest.mark.asyncio
async def test_hydrate_skips_orphan_row(tmp_db: DbPool) -> None:
    r = _reg()
    await persist_skill_ownership(tmp_db, "ghost", "web-scrape")  # ghost not registered
    assert await hydrate_skill_ownership(r, tmp_db) == 0  # one bad row never aborts


# ---------- purge (deletion path) -------------------------------------------


@pytest.mark.asyncio
async def test_purge_detaches_live_and_deletes_row(tmp_db: DbPool) -> None:
    r = _reg()
    attach_skill_to_owl(r, "scout", "web-scrape")
    await persist_skill_ownership(tmp_db, "scout", "web-scrape")
    detached = await purge_skill_ownership(tmp_db, "web-scrape", registry=r)
    assert detached == 1
    assert "web-scrape" not in r.get("scout").skills  # live detach
    assert await read_all_skill_ownership(tmp_db) == {}  # durable row gone
    # boot hydrate now re-attaches nothing — the phantom is closed
    assert await hydrate_skill_ownership(r, tmp_db) == 0


@pytest.mark.asyncio
async def test_purge_is_idempotent_noop(tmp_db: DbPool) -> None:
    r = _reg()
    assert await purge_skill_ownership(tmp_db, "never-owned", registry=r) == 0
