"""Story 3.3 — ``EvolutionCoordinator._apply_decay`` (FR-15, AD-1/AD-6).

"Unreinforced" == not a key in the cycle's proposed ``deltas`` dict — no new
persisted "last touched" state. ``decay_rate_per_week`` is a WEEKLY rate
applied as ``decay_rate_per_week / 7`` per DAILY batch run. Decay routes
through the SAME ``_checkpoint_validate_and_promote`` gated pipeline as any
other mutation (evolution_source="decay").
"""

from __future__ import annotations

import pytest

from stackowl.db.pool import DbPool
from stackowl.owls.dna import OwlDNA
from stackowl.owls.evolution import EvolutionCoordinator
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.providers.registry import ProviderRegistry
from tests._story_2_6_helpers import AlwaysFailShadowValidator, AlwaysPassShadowValidator


def _coordinator(reg: OwlRegistry, db: DbPool, *, gate_passes: bool) -> EvolutionCoordinator:
    validator = AlwaysPassShadowValidator() if gate_passes else AlwaysFailShadowValidator()
    return EvolutionCoordinator(
        db, ProviderRegistry(), reg, evolution_batch_size=1, shadow_validator=validator,
    )


@pytest.mark.asyncio
async def test_unreinforced_trait_decays_by_exact_weekly_rate_over_seven(
    tmp_db: DbPool,
) -> None:
    """A trait NOT in ``deltas``, away from its (default) authored anchor,
    decays by exactly ``(anchor - current) * (decay_rate_per_week / 7)`` and
    is promoted via a checkpoint tagged ``reason="decay"``."""
    reg = OwlRegistry()
    reg.register(
        OwlAgentManifest(
            name="drifter", role="analyst", system_prompt="Be helpful.",
            model_tier="fast",
            dna=OwlDNA(verbosity=0.60, decay_rate_per_week=0.07),  # daily fraction = 0.01
        )
    )
    coordinator = _coordinator(reg, tmp_db, gate_passes=True)

    promoted = await coordinator._apply_decay(reg.get("drifter"), reinforced_traits=frozenset())

    assert promoted is True
    expected = 0.60 + (0.50 - 0.60) * 0.01  # anchor defaults to NEUTRAL (0.5) — 0.599
    assert reg.get("drifter").dna.verbosity == pytest.approx(expected)

    rows = await tmp_db.fetch_all(
        "SELECT reason FROM learning_artifacts WHERE artifact_type = 'dna' AND artifact_id = ?",
        ("drifter",),
    )
    assert len(rows) == 1
    assert rows[0]["reason"] == "decay"


@pytest.mark.asyncio
async def test_reinforced_trait_untouched_even_when_far_from_anchor(tmp_db: DbPool) -> None:
    """AC #2 — a trait IN this cycle's ``deltas`` (reinforced) is skipped by
    decay outright, even though it sits far from its anchor."""
    reg = OwlRegistry()
    reg.register(
        OwlAgentManifest(
            name="reinforced", role="analyst", system_prompt="Be helpful.",
            model_tier="fast",
            dna=OwlDNA(curiosity=0.90, decay_rate_per_week=0.35),  # large daily fraction = 0.05
        )
    )
    coordinator = _coordinator(reg, tmp_db, gate_passes=True)

    promoted = await coordinator._apply_decay(
        reg.get("reinforced"), reinforced_traits=frozenset({"curiosity"}),
    )

    # curiosity was the ONLY trait away from anchor, and it was reinforced —
    # every other trait already sits at the default anchor, so there is
    # nothing left to decay.
    assert promoted is False
    assert reg.get("reinforced").dna.curiosity == pytest.approx(0.90)


@pytest.mark.asyncio
async def test_trait_already_at_anchor_no_decay_no_spurious_checkpoint(tmp_db: DbPool) -> None:
    """A trait already AT its (default) anchor value produces no decay and no
    new ``learning_artifacts`` row with ``reason="decay"``."""
    reg = OwlRegistry()
    reg.register(
        OwlAgentManifest(
            name="ataN", role="analyst", system_prompt="Be helpful.",
            model_tier="fast", dna=OwlDNA(decay_rate_per_week=0.5),
        )
    )
    coordinator = _coordinator(reg, tmp_db, gate_passes=True)

    promoted = await coordinator._apply_decay(reg.get("ataN"), reinforced_traits=frozenset())

    assert promoted is False
    rows = await tmp_db.fetch_all(
        "SELECT reason FROM learning_artifacts WHERE artifact_type = 'dna' AND artifact_id = ?",
        ("ataN",),
    )
    assert [r for r in rows if r["reason"] == "decay"] == []


@pytest.mark.asyncio
async def test_decay_gated_rejection_restores_and_does_not_apply(tmp_db: DbPool) -> None:
    """Decay passes through the SAME shadow-validation gate as any other
    mutation: a rejecting gate leaves the decayed value un-applied (restored),
    same auto-restore machinery Story 2.6 already tests."""
    reg = OwlRegistry()
    reg.register(
        OwlAgentManifest(
            name="gated", role="analyst", system_prompt="Be helpful.",
            model_tier="fast",
            dna=OwlDNA(formality=0.80, decay_rate_per_week=0.14),  # daily fraction = 0.02
        )
    )
    coordinator = _coordinator(reg, tmp_db, gate_passes=False)

    promoted = await coordinator._apply_decay(reg.get("gated"), reinforced_traits=frozenset())

    assert promoted is False
    # Gate rejected — the decayed value must NOT be applied; original restored.
    assert reg.get("gated").dna.formality == pytest.approx(0.80)
    rows = await tmp_db.fetch_all(
        "SELECT formality FROM owl_dna WHERE owl_name = ?", ("gated",),
    )
    assert len(rows) == 1
    assert rows[0]["formality"] == pytest.approx(0.80)


@pytest.mark.asyncio
async def test_evolve_one_bookkeeping_unaffected_by_decay(tmp_db: DbPool) -> None:
    """Regression: a full ``_evolve_one`` cycle where the main deltas path
    finds nothing (no attribution signal, no conversation excerpts) still
    reports the SAME skip semantics as before this story — decay running
    silently in the background must not change what ``_evolve_one`` returns,
    even though every mutable trait here sits away from the default anchor
    (so decay itself DOES fire, just without affecting the return value)."""
    reg = OwlRegistry()
    reg.register(
        OwlAgentManifest(
            name="quiet", role="analyst", system_prompt="Be helpful.",
            model_tier="fast",
            dna=OwlDNA(curiosity=0.65, decay_rate_per_week=0.07),
        )
    )
    coordinator = _coordinator(reg, tmp_db, gate_passes=True)

    # No messages seeded, no scored outcomes — both the attribution path and
    # the LLM-fallback's "not enough material" gate return no deltas.
    result = await coordinator._evolve_one(reg.get("quiet"))

    assert result is False  # main promotion result unchanged: no deltas → False
    # Decay itself DID fire in the background (curiosity was away from the
    # default anchor and unreinforced) — proven via its own checkpoint row.
    rows = await tmp_db.fetch_all(
        "SELECT reason FROM learning_artifacts WHERE artifact_type = 'dna' AND artifact_id = ?",
        ("quiet",),
    )
    assert any(r["reason"] == "decay" for r in rows)
