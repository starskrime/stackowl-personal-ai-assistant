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

from stackowl.commands.owls_command import OwlCommand, OwlsCommand
from stackowl.owls.directive_latch import DIRECTIVE_LATCH
from stackowl.owls.dna import OwlDNA
from stackowl.owls.dna_authored import capture_one_authored
from stackowl.owls.dna_hydrator import apply_dna_overlay
from stackowl.owls.dna_storage import upsert_owl_dna
from stackowl.owls.learning_artifact_store import LearningArtifactStore
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.owls.shadow_validator import (
    _DEFAULT_N_CONSECUTIVE,
    _DEFAULT_SAMPLE_SIZE,
    ShadowValidationResult,
    ShadowValidator,
)
from stackowl.pipeline.state import PipelineState
from stackowl.providers.registry import ProviderRegistry


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


def _cmd_with_providers(tmp_db):
    """Same as ``_cmd`` but wired with a real (lightweight, no-I/O) ProviderRegistry
    so dna-dry-run's DB/providers guard passes (Story 2.7)."""
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
    return (
        OwlsCommand(
            owl_registry=reg, db=tmp_db, event_bus=None, tool_registry=None,
            provider_registry=ProviderRegistry(),
        ),
        reg,
    )


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


# ------------------------------------------------------------ dna-dry-run (Story 2.7)


@pytest.mark.asyncio
async def test_dna_dry_run_no_provider_registry_unavailable(tmp_db):
    """Without a ProviderRegistry wired, dna-dry-run can't construct
    ShadowValidator — same honest "store unavailable" convention as db=None."""
    cmd, _ = _cmd(tmp_db)  # no provider_registry wired
    out = await cmd.handle("dna-dry-run scout", _state())
    assert out == "DNA store unavailable."


@pytest.mark.asyncio
async def test_dna_dry_run_pass_reports_pass_and_never_mutates(tmp_db, monkeypatch):
    cmd, reg = _cmd_with_providers(tmp_db)
    before = reg.get("scout").dna.model_dump()

    async def _stub_validate(self, owl_name, manifest, proposed_dna):  # noqa: ANN001, ARG001
        return ShadowValidationResult(
            passed=True, consecutive_non_regressions=3, n_replayed=3, failures=(),
        )

    monkeypatch.setattr(ShadowValidator, "validate", _stub_validate)

    out = await cmd.handle("dna-dry-run scout", _state())

    assert "PASS" in out
    # Read-only regression: registry DNA is bit-for-bit unchanged...
    assert reg.get("scout").dna.model_dump() == before
    # ...and nothing was ever persisted to owl_dna either.
    rows = await tmp_db.fetch_all("SELECT * FROM owl_dna WHERE owl_name = ?", ("scout",))
    assert rows == []


@pytest.mark.asyncio
async def test_dna_dry_run_fail_reports_failure_detail_and_never_mutates(tmp_db, monkeypatch):
    cmd, reg = _cmd_with_providers(tmp_db)
    before = reg.get("scout").dna.model_dump()

    async def _stub_validate(self, owl_name, manifest, proposed_dna):  # noqa: ANN001, ARG001
        return ShadowValidationResult(
            passed=False, consecutive_non_regressions=1, n_replayed=2,
            failures=({"input_text": "why is the sky blue", "reason": "quality_score=0.2 < 0.7"},),
        )

    monkeypatch.setattr(ShadowValidator, "validate", _stub_validate)

    out = await cmd.handle("dna-dry-run scout", _state())

    assert "FAIL" in out
    assert "quality_score=0.2 < 0.7" in out
    assert "why is the sky blue" in out
    assert reg.get("scout").dna.model_dump() == before
    rows = await tmp_db.fetch_all("SELECT * FROM owl_dna WHERE owl_name = ?", ("scout",))
    assert rows == []


@pytest.mark.asyncio
async def test_dna_dry_run_uses_module_default_config_not_override(tmp_db, monkeypatch):
    """AC #3 / AD-3 — the dry-run must construct ShadowValidator with the exact
    same module-level defaults the real promotion path uses, never a looser
    caller-specific override."""
    cmd, _ = _cmd_with_providers(tmp_db)
    captured: dict[str, int] = {}

    async def _spy_validate(self, owl_name, manifest, proposed_dna):  # noqa: ANN001, ARG001
        captured["n_consecutive_required"] = self.n_consecutive_required
        captured["sample_size"] = self.sample_size
        return ShadowValidationResult(
            passed=True, consecutive_non_regressions=3, n_replayed=3, failures=(),
        )

    monkeypatch.setattr(ShadowValidator, "validate", _spy_validate)

    await cmd.handle("dna-dry-run scout", _state())

    assert captured["n_consecutive_required"] == _DEFAULT_N_CONSECUTIVE
    assert captured["sample_size"] == _DEFAULT_SAMPLE_SIZE


@pytest.mark.asyncio
async def test_dna_dry_run_wired_in_both_owls_and_owl_commands(tmp_db, monkeypatch):
    """Story 2.2's double-registration pattern, mirrored for dna-dry-run:
    reachable from BOTH /owls dna-dry-run and /owl dna-dry-run, not just one
    surface."""

    async def _stub_validate(self, owl_name, manifest, proposed_dna):  # noqa: ANN001, ARG001
        return ShadowValidationResult(
            passed=True, consecutive_non_regressions=3, n_replayed=3, failures=(),
        )

    monkeypatch.setattr(ShadowValidator, "validate", _stub_validate)

    reg = OwlRegistry()
    reg.register(
        OwlAgentManifest(
            name="scout", role="r", system_prompt="p", model_tier="fast",
            dna=OwlDNA(challenge_level=0.5),
        ),
        source_name="t",
    )
    owls_cmd = OwlsCommand(owl_registry=reg, db=tmp_db, provider_registry=ProviderRegistry())
    owl_cmd = OwlCommand(owl_registry=reg, db=tmp_db, provider_registry=ProviderRegistry())

    assert "dna-dry-run" in {s.name for s in owls_cmd.meta.subcommands}
    assert "dna-dry-run" in {s.name for s in owl_cmd.meta.subcommands}

    out1 = await owls_cmd.handle("dna-dry-run scout", _state())
    out2 = await owl_cmd.handle("dna-dry-run scout", _state())
    assert "PASS" in out1
    assert "PASS" in out2
