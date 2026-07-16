"""Task 4 — EvolutionCoordinator live-refresh + bound_dna governor.

Headline new assertions over test_story_4_3:
  (1) reg.get(owl).dna is updated LIVE after evolution (was only checking SQLite).
  (2) A big proposed delta (here +0.5 curiosity) is capped to MAX_DELTA (0.05).
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

import stackowl.owls.evolution as evolution_module
from stackowl.config.test_mode import TestModeGuard
from stackowl.db.pool import DbPool
from stackowl.owls.dna import OwlDNA
from stackowl.owls.dna_attribution import AttributionReport, DnaAttributor
from stackowl.owls.evolution import EvolutionCoordinator
from stackowl.owls.evolution_limits import MAX_DELTA
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.providers.mock_provider import MockProvider
from stackowl.providers.registry import ProviderRegistry
from stackowl.scheduler.job import Job
from tests._story_2_6_helpers import AlwaysFailShadowValidator, AlwaysPassShadowValidator

# ---------------------------------------------------------------------------
# helpers (mirror test_story_4_3 exactly)
# ---------------------------------------------------------------------------


def _job(job_id: str) -> Job:
    return Job(
        job_id=job_id,
        handler_name="evolution_batch",
        schedule="*/10 * * * *",
        idempotency_key=job_id,
        last_run_at=None,
        next_run_at=datetime.now(UTC).isoformat(),
        status="pending",
    )


async def _seed_messages(db: DbPool, owl_name: str, count: int) -> None:
    """Insert ``count`` user messages tied to a conversation owned by ``owl_name``."""
    conv_id = uuid.uuid4().hex
    now = datetime.now(UTC).isoformat()
    await db.execute(
        "INSERT INTO conversations (id, session_id, owl_name, started_at, message_count) "
        "VALUES (?, ?, ?, ?, ?)",
        (conv_id, f"sess-{owl_name}", owl_name, now, count),
    )
    for i in range(count):
        await db.execute(
            "INSERT INTO messages (id, conversation_id, role, content, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (uuid.uuid4().hex, conv_id, "user", f"sample message {i}", now),
        )


# ---------------------------------------------------------------------------
# test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evolution_refreshes_live_registry_and_bounds(tmp_db: DbPool) -> None:
    """After evolution:
    (a) The live registry carries the bounded new DNA — not just SQLite.
    (b) A proposed delta of +0.5 curiosity is clamped to MAX_DELTA (0.05).
    """
    was_active = TestModeGuard.is_active()
    TestModeGuard.deactivate()
    try:
        provider_registry = ProviderRegistry()
        # Propose a curiosity delta of +0.5 — far beyond MAX_DELTA (0.05).
        # bound_dna must clamp it to exactly +MAX_DELTA → 0.50 + 0.05 = 0.55.
        mock = MockProvider(
            name="mock-fast",
            canned_text=(
                '{"challenge_level": 0.0, "verbosity": 0.0, "curiosity": 0.5, '
                '"formality": 0.0, "creativity": 0.0, "precision": 0.0}'
            ),
        )
        provider_registry.register_mock("mock-fast", mock, tier="fast")

        reg = OwlRegistry()
        reg.register(
            OwlAgentManifest(
                name="nora",
                role="analyst",
                system_prompt="Be helpful.",
                model_tier="fast",
                dna=OwlDNA(curiosity=0.50),
            )
        )

        await _seed_messages(tmp_db, "nora", count=3)

        # Story 2.6 — the real ShadowValidator fails CLOSED on cold start (no
        # scored task_outcomes seeded here, only messages). This test is about
        # bound_dna clamping, not gate mechanics, so it stubs the gate to always
        # pass — see tests/_story_2_6_helpers.py.
        coordinator = EvolutionCoordinator(
            tmp_db, provider_registry, reg, evolution_batch_size=3,
            shadow_validator=AlwaysPassShadowValidator(),
        )
        result = await coordinator.execute(_job("job-feedback-t4"))
        assert result.success is True

        expected_curiosity = 0.50 + MAX_DELTA  # 0.55 — clamped by bound_dna

        # (a) Live registry reflects the bounded value.
        live = reg.get("nora").dna.curiosity
        assert abs(live - expected_curiosity) < 1e-9, (
            f"Live registry not updated: got {live}, expected {expected_curiosity}"
        )

        # (b) SQLite also reflects the bounded value (persisted == live).
        rows = await tmp_db.fetch_all(
            "SELECT curiosity FROM owl_dna WHERE owl_name = ?", ("nora",)
        )
        assert len(rows) == 1
        assert abs(rows[0]["curiosity"] - expected_curiosity) < 1e-9, (
            f"DB value not bounded: got {rows[0]['curiosity']}, expected {expected_curiosity}"
        )

        # (c) Story 2.6 (NFR-5) — the checkpoint step is unchanged: a
        # learning_artifacts row for this promotion still exists.
        cps = await tmp_db.fetch_all(
            "SELECT checkpoint_id FROM learning_artifacts "
            "WHERE artifact_type = 'dna' AND artifact_id = ?", ("nora",),
        )
        assert len(cps) == 1
    finally:
        if was_active:
            TestModeGuard.activate()


# ---------------------------------------------------------------------------
# Story 2.4 — signal-strength-tiered clamp: prove the two known callers
# (attribution vs. LLM-fallback) are wired to the correct SignalStrength tag
# by observing the resulting DNA magnitude for an identical raw delta
# (0.02, well under MAX_DELTA so the rate cap doesn't mask the difference).
# ---------------------------------------------------------------------------


class _FixedAttributor(DnaAttributor):
    """Always reports a fixed +0.02 curiosity delta — bypasses seeding 20+
    scored TaskOutcome rows just to exercise the attribution branch's signal
    tag (evolution.py's `_try_attribution` only calls `.attribute(...)`)."""

    def attribute(self, owl_name: str, current_dna: OwlDNA, outcomes: list) -> AttributionReport:  # type: ignore[override]
        return AttributionReport(
            owl_name=owl_name, n_scored_outcomes=len(outcomes),
            deltas={"curiosity": 0.02}, per_trait=(),
            explore_fired=False, explore_trait=None, fallback_reason=None,
        )


@pytest.mark.asyncio
async def test_attribution_path_tagged_verified_llm_fallback_tagged_llm_quality(
    tmp_db: DbPool,
) -> None:
    """Same raw +0.02 curiosity delta, only the source path differs:
    attribution → SignalStrength.VERIFIED (unscaled, 0.02) vs.
    llm_fallback → SignalStrength.LLM_QUALITY (0.3x, 0.006)."""
    was_active = TestModeGuard.is_active()
    TestModeGuard.deactivate()
    try:
        provider_registry = ProviderRegistry()
        mock = MockProvider(
            name="mock-fast",
            canned_text=(
                '{"challenge_level": 0.0, "verbosity": 0.0, "curiosity": 0.02, '
                '"formality": 0.0, "creativity": 0.0, "precision": 0.0}'
            ),
        )
        provider_registry.register_mock("mock-fast", mock, tier="fast")

        # --- attribution path: fixed attributor short-circuits the LLM fallback ---
        attrib_reg = OwlRegistry()
        attrib_reg.register(
            OwlAgentManifest(
                name="owlattrib", role="analyst", system_prompt="Be helpful.",
                model_tier="fast", dna=OwlDNA(curiosity=0.50),
            )
        )
        attrib_coordinator = EvolutionCoordinator(
            tmp_db, provider_registry, attrib_reg, evolution_batch_size=1,
            attributor=_FixedAttributor(),
            shadow_validator=AlwaysPassShadowValidator(),
        )
        result = await attrib_coordinator.execute(_job("job-signal-attrib"))
        assert result.success is True
        attrib_curiosity = attrib_reg.get("owlattrib").dna.curiosity

        # --- llm_fallback path: real (empty-signal) attributor falls through ---
        llm_reg = OwlRegistry()
        llm_reg.register(
            OwlAgentManifest(
                name="owlllm", role="analyst", system_prompt="Be helpful.",
                model_tier="fast", dna=OwlDNA(curiosity=0.50),
            )
        )
        await _seed_messages(tmp_db, "owlllm", count=3)
        llm_coordinator = EvolutionCoordinator(
            tmp_db, provider_registry, llm_reg, evolution_batch_size=1,
            shadow_validator=AlwaysPassShadowValidator(),
        )
        result = await llm_coordinator.execute(_job("job-signal-llm"))
        assert result.success is True
        llm_curiosity = llm_reg.get("owlllm").dna.curiosity

        assert abs(attrib_curiosity - 0.52) < 1e-9   # VERIFIED: full 0.02 delta
        assert abs(llm_curiosity - 0.506) < 1e-9      # LLM_QUALITY: 0.02 * 0.3
        assert attrib_curiosity > llm_curiosity
    finally:
        if was_active:
            TestModeGuard.activate()


# ---------------------------------------------------------------------------
# Story 2.6 — the shadow gate rejects the mutation: no promotion, checkpoint
# is restored (FR-10, AC #2).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gate_rejects_mutation_restores_checkpoint_and_logs_warning(
    tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ShadowValidator.validate() reports ``passed=False``:
    - the owl's live/DB DNA stays exactly as it was pre-mutation (no promotion)
    - an ERROR is logged (Story 2.7 AC #1 — elevated from WARNING, visible
      without a human going looking for it) with enriched structured fields
    - LearningArtifactStore.restore() is ACTUALLY called (spied, not just
      inferred from the unchanged value — see Story 2.6 Dev Notes on why
      restore is a real step even though it's usually a no-op today)
    """
    provider_registry = ProviderRegistry()
    reg = OwlRegistry()
    reg.register(
        OwlAgentManifest(
            name="owlrejected", role="analyst", system_prompt="Be helpful.",
            model_tier="fast", dna=OwlDNA(curiosity=0.50),
        )
    )

    coordinator = EvolutionCoordinator(
        tmp_db, provider_registry, reg, evolution_batch_size=1,
        attributor=_FixedAttributor(),  # deterministic +0.02 curiosity delta
        shadow_validator=AlwaysFailShadowValidator(),
    )

    restore_calls: list[tuple[str, str, str]] = []
    original_restore = coordinator._learning_store.restore

    async def _spy_restore(
        artifact_type: str, artifact_id: str, checkpoint_id: str,
    ) -> dict[str, object]:
        restore_calls.append((artifact_type, artifact_id, checkpoint_id))
        return await original_restore(artifact_type, artifact_id, checkpoint_id)

    coordinator._learning_store.restore = _spy_restore  # type: ignore[method-assign]

    errors: list[str] = []
    fields: list[dict[str, object]] = []

    def _capture_error(msg: str, *a: object, **k: object) -> None:
        errors.append(str(msg))
        fields.append(k["extra"]["_fields"])  # type: ignore[index]

    monkeypatch.setattr(evolution_module.log.owls, "error", _capture_error)
    promoted = await coordinator._evolve_one(reg.get("owlrejected"))

    assert promoted is False

    # No promotion: live registry DNA is exactly the pre-mutation baseline.
    assert reg.get("owlrejected").dna.curiosity == pytest.approx(0.50)

    # Restore actually ran (not inferred) — exactly once, for this owl/artifact.
    assert len(restore_calls) == 1
    assert restore_calls[0][0] == "dna"
    assert restore_calls[0][1] == "owlrejected"

    # restore() -> _persist_dna wrote the baseline back to owl_dna (proves the
    # restore-and-reaffirm path actually executed, not skipped as a no-op).
    rows = await tmp_db.fetch_all(
        "SELECT curiosity FROM owl_dna WHERE owl_name = ?", ("owlrejected",)
    )
    assert len(rows) == 1
    assert rows[0]["curiosity"] == pytest.approx(0.50)

    assert any("shadow gate REJECTED" in w for w in errors)

    # Story 2.7 (AC #1) — the rejection record carries enough structured
    # detail to be queryable/countable by `jq`, not just a free-text line.
    assert len(fields) == 1
    rejection_fields = fields[0]
    assert rejection_fields["owl"] == "owlrejected"
    assert rejection_fields["checkpoint_id"]
    assert rejection_fields["n_replayed"] == 0
    assert rejection_fields["consecutive_non_regressions"] == 0
    assert rejection_fields["n_consecutive_required"] == 3  # ShadowValidator's module default
    assert rejection_fields["failures"] == []
