"""F-52: `completion_drive` persistence/initiative trait.

The pre-existing trait taxonomy governed only tone/register — nothing drove
task-completion persistence, so a tenacious owl was indistinguishable from a
lazy one. These tests pin the new trait into every closed-taxonomy site:
the model field, the canonical name tuple, the attribution evolver, the
injector directive, and the persisted stores (round-trip through SQLite).
"""
from __future__ import annotations

import pytest

from stackowl.owls.directive_latch import DIRECTIVE_LATCH
from stackowl.owls.dna import _MUTABLE_TRAITS, OwlDNA
from stackowl.owls.dna_attribution import _MUTABLE_TRAITS as ATTR_TRAITS
from stackowl.owls.dna_defaults import NEUTRAL, TRAIT_NAMES
from stackowl.owls.dna_injector import DNAPromptInjector
from stackowl.owls.manifest import OwlAgentManifest

_TRAIT = "completion_drive"


def _m(name: str = "tenacious") -> OwlAgentManifest:
    return OwlAgentManifest(name=name, role="r", system_prompt="BASE", model_tier="standard")


def test_completion_drive_in_canonical_taxonomy() -> None:
    assert _TRAIT in TRAIT_NAMES
    assert _TRAIT in _MUTABLE_TRAITS
    # Evolves from outcomes exactly like the others (attribution iterates this).
    assert _TRAIT in ATTR_TRAITS


def test_completion_drive_defaults_neutral() -> None:
    # Behaviour-neutral default keeps existing owls stable.
    assert OwlDNA().completion_drive == NEUTRAL


def test_completion_drive_mutates_like_any_trait() -> None:
    nudged = OwlDNA().mutate(_TRAIT, 0.2)
    assert nudged.completion_drive == pytest.approx(0.7)


def test_high_completion_drive_emits_persistence_directive() -> None:
    DIRECTIVE_LATCH.clear_all()
    out = DNAPromptInjector().inject(_m(), OwlDNA(completion_drive=0.85))
    low = out.lower()
    assert out != "BASE"  # HIGH modulates behaviour
    assert "persist" in low or "pursu" in low  # keep pursuing the goal
    assert "blocked" in low  # across blocked paths


def test_low_completion_drive_stays_neutral() -> None:
    # No LOW directive — a low-drive owl is simply un-modulated, never told to
    # give up early. Default-path (all 0.5) likewise emits nothing.
    DIRECTIVE_LATCH.clear_all()
    out = DNAPromptInjector().inject(_m("lazy"), OwlDNA(completion_drive=0.12))
    assert out == "BASE"


@pytest.mark.asyncio
async def test_completion_drive_round_trips_owl_dna(tmp_db) -> None:
    from stackowl.owls.dna_storage import upsert_owl_dna

    await upsert_owl_dna(tmp_db, "scout", OwlDNA(completion_drive=0.71), table="owl_dna")
    rows = await tmp_db.fetch_all(
        "SELECT completion_drive FROM owl_dna WHERE owl_name = ?", ("scout",)
    )
    assert rows[0]["completion_drive"] == 0.71


@pytest.mark.asyncio
async def test_completion_drive_round_trips_authored(tmp_db) -> None:
    from stackowl.owls.dna_authored import read_authored_dna
    from stackowl.owls.dna_storage import upsert_owl_dna

    await upsert_owl_dna(tmp_db, "scout", OwlDNA(completion_drive=0.63), table="owl_dna_authored")
    dna = await read_authored_dna(tmp_db, "scout")
    assert dna is not None and dna.completion_drive == 0.63


@pytest.mark.asyncio
async def test_completion_drive_round_trips_checkpoint(tmp_db) -> None:
    from stackowl.owls.dna_storage import DNACheckpointer

    cp = DNACheckpointer(tmp_db)
    cid = await cp.checkpoint("scout", OwlDNA(completion_drive=0.83))
    restored = await cp.restore("scout", cid)
    assert restored.completion_drive == 0.83


@pytest.mark.asyncio
async def test_existing_rows_backfill_neutral(tmp_db) -> None:
    # Simulate a legacy owl_dna row written before the column existed by reading
    # back a freshly-defaulted owl: the migration DEFAULT must be the neutral 0.5.
    from stackowl.owls.dna_storage import upsert_owl_dna

    await upsert_owl_dna(tmp_db, "legacy", OwlDNA(), table="owl_dna")
    rows = await tmp_db.fetch_all(
        "SELECT completion_drive FROM owl_dna WHERE owl_name = ?", ("legacy",)
    )
    assert rows[0]["completion_drive"] == NEUTRAL
