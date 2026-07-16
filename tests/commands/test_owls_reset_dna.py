"""Task 9 — /owls reset-dna + current-vs-authored readout.
Story 2.2 — /owls dna-restore <name> <checkpoint_id> YES.

Tests:
  - reset-dna requires YES confirmation
  - reset-dna reverts to authored baseline + live-refreshes registry
  - reset-dna with no authored baseline returns informative message
  - reset-dna clears the DIRECTIVE_LATCH for the owl
  - /owls dna <name> shows authored baseline alongside current values
  - dna-restore requires YES confirmation, leaves DNA unchanged until confirmed
  - dna-restore restores exact checkpointed trait values + live-refreshes registry
  - dna-restore on an unknown checkpoint_id fails loudly, DNA unchanged
"""
from __future__ import annotations

import pytest

from stackowl.commands.owls_command import OwlsCommand
from stackowl.owls.directive_latch import DIRECTIVE_LATCH
from stackowl.owls.dna import OwlDNA
from stackowl.owls.dna_authored import capture_one_authored
from stackowl.owls.dna_hydrator import apply_dna_overlay
from stackowl.owls.dna_storage import upsert_owl_dna
from stackowl.owls.learning_artifact_store import LearningArtifactStore
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.state import PipelineState


def _state() -> PipelineState:
    return PipelineState(
        trace_id="trace-test",
        session_id="sess-test",
        input_text="hello",
        channel="cli",
        owl_name="secretary",
        pipeline_step="receive",
    )


def _cmd(tmp_db):
    reg = OwlRegistry()
    reg.register(
        OwlAgentManifest(
            name="scout",
            role="r",
            system_prompt="p",
            model_tier="fast",
            dna=OwlDNA(challenge_level=0.5),
        ),
        source_name="t",
    )
    return OwlsCommand(owl_registry=reg, db=tmp_db, event_bus=None, tool_registry=None), reg


@pytest.mark.asyncio
async def test_reset_dna_requires_confirm(tmp_db):
    cmd, _ = _cmd(tmp_db)
    out = await cmd.handle("reset-dna scout", _state())
    assert "YES" in out


@pytest.mark.asyncio
async def test_reset_dna_reverts_to_authored_and_live_refreshes(tmp_db):
    cmd, reg = _cmd(tmp_db)
    await capture_one_authored(tmp_db, "scout", OwlDNA(challenge_level=0.5))
    await upsert_owl_dna(tmp_db, "scout", OwlDNA(challenge_level=0.8), table="owl_dna")
    apply_dna_overlay(reg, "scout", OwlDNA(challenge_level=0.8))
    out = await cmd.handle("reset-dna scout YES", _state())
    assert "reset" in out.lower()
    assert reg.get("scout").dna.challenge_level == pytest.approx(0.5)
    rows = await tmp_db.fetch_all(
        "SELECT challenge_level FROM owl_dna WHERE owl_name = ?", ("scout",)
    )
    assert rows[0]["challenge_level"] == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_reset_dna_no_authored_baseline(tmp_db):
    cmd, _ = _cmd(tmp_db)
    out = await cmd.handle("reset-dna scout YES", _state())
    assert "no authored" in out.lower()


@pytest.mark.asyncio
async def test_reset_dna_clears_latch(tmp_db):
    cmd, reg = _cmd(tmp_db)
    await capture_one_authored(tmp_db, "scout", OwlDNA(challenge_level=0.5))
    # Ensure clean state first, then seed latch ON (0.72 >= HIGH_ENTER 0.62 → True)
    DIRECTIVE_LATCH.reset_owl("scout")
    DIRECTIVE_LATCH.high_state("scout", "challenge_level", 0.72)
    await cmd.handle("reset-dna scout YES", _state())
    # After reset_owl the latch is cleared; next call cold-seeds from the given value.
    # 0.50 < HIGH_ENTER (0.62) → seeds as False
    assert DIRECTIVE_LATCH.high_state("scout", "challenge_level", 0.50) is False


@pytest.mark.asyncio
async def test_dna_readout_shows_authored(tmp_db):
    cmd, reg = _cmd(tmp_db)
    await capture_one_authored(tmp_db, "scout", OwlDNA(challenge_level=0.5))
    await upsert_owl_dna(tmp_db, "scout", OwlDNA(challenge_level=0.8), table="owl_dna")
    apply_dna_overlay(reg, "scout", OwlDNA(challenge_level=0.8))
    out = await cmd.handle("dna scout", _state())
    assert "authored" in out.lower()


# ------------------------------------------------------------ dna-restore (Story 2.2)

@pytest.mark.asyncio
async def test_dna_restore_requires_confirm(tmp_db):
    cmd, _ = _cmd(tmp_db)
    store = LearningArtifactStore(tmp_db)
    checkpoint_id = await store.checkpoint(
        "dna", "scout", OwlDNA(challenge_level=0.3).model_dump(), reason="test"
    )
    out = await cmd.handle(f"dna-restore scout {checkpoint_id}", _state())
    assert "YES" in out


@pytest.mark.asyncio
async def test_dna_restore_reverts_to_checkpoint_and_live_refreshes(tmp_db):
    cmd, reg = _cmd(tmp_db)
    store = LearningArtifactStore(tmp_db)
    checkpoint_id = await store.checkpoint(
        "dna", "scout", OwlDNA(challenge_level=0.3).model_dump(), reason="test"
    )
    # Mutate the owl's live DNA to something different than the checkpoint.
    await upsert_owl_dna(tmp_db, "scout", OwlDNA(challenge_level=0.9), table="owl_dna")
    apply_dna_overlay(reg, "scout", OwlDNA(challenge_level=0.9))

    out = await cmd.handle(f"dna-restore scout {checkpoint_id} YES", _state())

    assert "restored" in out.lower()
    assert checkpoint_id in out
    assert reg.get("scout").dna.challenge_level == pytest.approx(0.3)
    rows = await tmp_db.fetch_all(
        "SELECT challenge_level FROM owl_dna WHERE owl_name = ?", ("scout",)
    )
    assert rows[0]["challenge_level"] == pytest.approx(0.3)


@pytest.mark.asyncio
async def test_dna_restore_unconfirmed_leaves_dna_unchanged(tmp_db):
    cmd, reg = _cmd(tmp_db)
    store = LearningArtifactStore(tmp_db)
    checkpoint_id = await store.checkpoint(
        "dna", "scout", OwlDNA(challenge_level=0.3).model_dump(), reason="test"
    )
    await upsert_owl_dna(tmp_db, "scout", OwlDNA(challenge_level=0.9), table="owl_dna")
    apply_dna_overlay(reg, "scout", OwlDNA(challenge_level=0.9))

    out = await cmd.handle(f"dna-restore scout {checkpoint_id}", _state())

    assert "YES" in out
    assert reg.get("scout").dna.challenge_level == pytest.approx(0.9)
    rows = await tmp_db.fetch_all(
        "SELECT challenge_level FROM owl_dna WHERE owl_name = ?", ("scout",)
    )
    assert rows[0]["challenge_level"] == pytest.approx(0.9)


@pytest.mark.asyncio
async def test_dna_restore_unknown_checkpoint_fails_loud(tmp_db):
    cmd, reg = _cmd(tmp_db)
    await upsert_owl_dna(tmp_db, "scout", OwlDNA(challenge_level=0.9), table="owl_dna")
    apply_dna_overlay(reg, "scout", OwlDNA(challenge_level=0.9))

    out = await cmd.handle("dna-restore scout not-a-real-checkpoint YES", _state())

    assert out.startswith("✗ /owls dna-restore:")
    assert reg.get("scout").dna.challenge_level == pytest.approx(0.9)
    rows = await tmp_db.fetch_all(
        "SELECT challenge_level FROM owl_dna WHERE owl_name = ?", ("scout",)
    )
    assert rows[0]["challenge_level"] == pytest.approx(0.9)
