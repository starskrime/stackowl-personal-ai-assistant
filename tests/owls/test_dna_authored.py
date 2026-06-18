"""Tests for the authored-DNA store (capture/read round-trip, coercion, fail-safe)."""
from __future__ import annotations

import pytest
from stackowl.owls.dna import OwlDNA
from stackowl.owls.dna_authored import capture_one_authored, capture_authored_dna, read_authored_dna
from stackowl.owls.registry import OwlRegistry
from stackowl.owls.manifest import OwlAgentManifest


def _reg_with(name: str, dna: OwlDNA) -> OwlRegistry:
    reg = OwlRegistry()
    reg.register(
        OwlAgentManifest(name=name, role=name, system_prompt="p", model_tier="fast", dna=dna),
        source_name="t",
    )
    return reg


@pytest.mark.asyncio
async def test_capture_then_read_round_trip(tmp_db):
    await capture_one_authored(tmp_db, "scout", OwlDNA(challenge_level=0.75, precision=0.66))
    got = await read_authored_dna(tmp_db, "scout")
    assert got is not None and got.challenge_level == 0.75 and got.precision == 0.66


@pytest.mark.asyncio
async def test_read_missing_returns_none(tmp_db):
    assert await read_authored_dna(tmp_db, "ghost") is None


@pytest.mark.asyncio
async def test_capture_authored_dna_boot_pass_covers_all_owls(tmp_db):
    reg = _reg_with("scout", OwlDNA(curiosity=0.8))
    await capture_authored_dna(reg, tmp_db)
    got = await read_authored_dna(tmp_db, "scout")
    assert got is not None and got.curiosity == 0.8


@pytest.mark.asyncio
async def test_recreate_same_name_overwrites_anchor(tmp_db):
    await capture_one_authored(tmp_db, "scout", OwlDNA(verbosity=0.2))
    await capture_one_authored(tmp_db, "scout", OwlDNA(verbosity=0.9))
    got = await read_authored_dna(tmp_db, "scout")
    assert got is not None and got.verbosity == 0.9


@pytest.mark.asyncio
async def test_read_coerces_corrupt_row(tmp_db):
    # SQLite REAL NOT NULL rejects NaN (converts to NULL → constraint failure),
    # so inject an out-of-range value (1.5) which SQLite can store but _coerce_dna
    # must clamp to [0.0, 1.0].
    await tmp_db.execute(
        "INSERT INTO owl_dna_authored (owl_name, challenge_level, verbosity, curiosity, formality, creativity, precision, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        ("bad", 1.5, 0.5, 0.5, 0.5, 0.5, 0.5, "t"),
    )
    got = await read_authored_dna(tmp_db, "bad")
    assert got is not None and 0.0 <= got.challenge_level <= 1.0
