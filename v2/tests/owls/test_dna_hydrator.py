"""Tests for apply_dna_overlay + DnaHydrator (persona-evo T2)."""

from __future__ import annotations

import pytest

from stackowl.owls.dna import OwlDNA
from stackowl.owls.dna_hydrator import apply_dna_overlay, hydrate_dna
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry


def _reg() -> OwlRegistry:
    r = OwlRegistry.with_default_secretary()
    r.register(OwlAgentManifest(name="scout", role="research", system_prompt="P", model_tier="fast"))
    return r


def test_apply_dna_overlay_is_dna_only() -> None:
    r = _reg()
    assert apply_dna_overlay(r, "scout", OwlDNA(curiosity=0.7))
    m = r.get("scout")
    assert m.dna.curiosity == 0.7
    assert m.role == "research" and m.system_prompt == "P"   # identity untouched


def test_apply_dna_overlay_orphan_returns_false() -> None:
    assert apply_dna_overlay(_reg(), "ghost", OwlDNA()) is False


async def _insert(db: object, name: str, **traits: float) -> None:
    cols = {"challenge_level": 0.5, "verbosity": 0.5, "curiosity": 0.5,
            "formality": 0.5, "creativity": 0.5, "precision": 0.5}
    cols.update(traits)
    await db.execute(  # type: ignore[union-attr]
        "INSERT INTO owl_dna (owl_name, challenge_level, verbosity, curiosity, formality, "
        "creativity, precision, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (name, cols["challenge_level"], cols["verbosity"], cols["curiosity"], cols["formality"],
         cols["creativity"], cols["precision"], "2026-06-06T00:00:00"),
    )


@pytest.mark.asyncio
async def test_hydrate_overlays_persisted(tmp_db: object) -> None:
    r = _reg()
    await _insert(tmp_db, "scout", curiosity=0.65)
    assert await hydrate_dna(r, tmp_db) == 1  # type: ignore[arg-type]
    assert r.get("scout").dna.curiosity == 0.65


@pytest.mark.asyncio
async def test_hydrate_failsafe_on_out_of_range(tmp_db: object) -> None:
    r = _reg()
    await _insert(tmp_db, "scout", challenge_level=9.9)
    await hydrate_dna(r, tmp_db)                               # must NOT crash  # type: ignore[arg-type]
    assert 0.0 <= r.get("scout").dna.challenge_level <= 1.0     # clamped


@pytest.mark.asyncio
async def test_hydrate_skips_orphan(tmp_db: object) -> None:
    r = _reg()
    await _insert(tmp_db, "ghost", curiosity=0.6)
    assert await hydrate_dna(r, tmp_db) == 0                    # ghost not in registry  # type: ignore[arg-type]
