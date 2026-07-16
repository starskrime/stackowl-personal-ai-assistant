"""Story 3.1 — ``EvolutionCoordinator.evolve_one_owl_now`` (FR-12/FR-13/AD-5).

Two things must hold, and both are regression-tested here (not just "happens
to be true today"):

1. AD-5 — this path NEVER calls ``DnaAttributor.attribute``/``_try_attribution``,
   not even indirectly. It is a structurally separate code path from
   ``_evolve_one``'s attribution-first branch, forcing the LLM-fallback
   unconditionally.
2. AD-1/AD-3 — it reuses ``_checkpoint_validate_and_promote``, Story 2.6's ONE
   promotion path, so it is gated by the shadow-validation gate from day one
   (proven by driving both an accepting and a rejecting stub gate).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from stackowl.config.test_mode import TestModeGuard
from stackowl.db.pool import DbPool
from stackowl.exceptions import OwlNotFoundError
from stackowl.owls.dna import OwlDNA
from stackowl.owls.dna_attribution import AttributionReport, DnaAttributor
from stackowl.owls.evolution import EvolutionCoordinator
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.providers.mock_provider import MockProvider
from stackowl.providers.registry import ProviderRegistry
from tests._story_2_6_helpers import AlwaysFailShadowValidator, AlwaysPassShadowValidator


async def _seed_messages(db: DbPool, owl_name: str, count: int) -> None:
    """Insert ``count`` user messages tied to a conversation owned by ``owl_name``
    (mirrors ``test_evolution_feedback.py``'s helper — enough excerpts for
    ``_llm_fallback`` to not bail on the "too little material" gate)."""
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


class _SpyAttributor(DnaAttributor):
    """Records every call to ``.attribute`` — the regression probe for AD-5's
    "never branches on DnaAttributor's sample count" guarantee."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[str] = []

    def attribute(self, owl_name: str, current_dna: OwlDNA, outcomes: list) -> AttributionReport:  # type: ignore[override]
        self.calls.append(owl_name)
        # If this were ever called, it would return a confident delta — a
        # canary that would flip a naive "assert no mutation" test green for
        # the wrong reason. The .calls spy is the real assertion.
        return AttributionReport(
            owl_name=owl_name, n_scored_outcomes=999,
            deltas={"curiosity": 0.9}, per_trait=(),
            explore_fired=False, explore_trait=None, fallback_reason=None,
        )


def _mock_registry_and_provider(owl_name: str, curiosity_delta: float = 0.02) -> tuple[OwlRegistry, ProviderRegistry]:
    reg = OwlRegistry()
    reg.register(
        OwlAgentManifest(
            name=owl_name, role="analyst", system_prompt="Be helpful.",
            model_tier="fast", dna=OwlDNA(curiosity=0.50),
        )
    )
    provider_registry = ProviderRegistry()
    mock = MockProvider(
        name="mock-fast",
        canned_text=(
            '{"challenge_level": 0.0, "verbosity": 0.0, '
            f'"curiosity": {curiosity_delta}, '
            '"formality": 0.0, "creativity": 0.0, "precision": 0.0}'
        ),
    )
    provider_registry.register_mock("mock-fast", mock, tier="fast")
    return reg, provider_registry


@pytest.mark.asyncio
async def test_evolve_one_owl_now_never_calls_attribution(tmp_db: DbPool) -> None:
    """AD-5 regression: the attributor is NEVER consulted on this path, even
    though the (spy) attributor would happily return a confident delta if it
    were."""
    was_active = TestModeGuard.is_active()
    TestModeGuard.deactivate()
    try:
        reg, provider_registry = _mock_registry_and_provider("nora")
        await _seed_messages(tmp_db, "nora", count=3)
        spy = _SpyAttributor()

        coordinator = EvolutionCoordinator(
            tmp_db, provider_registry, reg, evolution_batch_size=3,
            attributor=spy,
            shadow_validator=AlwaysPassShadowValidator(),
        )
        promoted = await coordinator.evolve_one_owl_now("nora")

        assert promoted is True
        assert spy.calls == [], (
            f"evolve_one_owl_now called DnaAttributor.attribute: {spy.calls} — "
            "violates AD-5 (must force the LLM-fallback path unconditionally)"
        )
        # Confirms the LLM-fallback delta landed at LLM_QUALITY signal
        # strength (0.02 * 0.3 = 0.006, not the spy's poison 0.9).
        assert reg.get("nora").dna.curiosity == pytest.approx(0.506)
    finally:
        if was_active:
            TestModeGuard.activate()


@pytest.mark.asyncio
async def test_evolve_one_owl_now_promotes_through_the_shared_gate(tmp_db: DbPool) -> None:
    """AD-1/AD-3: a passing shadow gate persists the mutation to both the live
    registry and SQLite, and records a checkpoint — the same effects
    ``_checkpoint_validate_and_promote`` produces for the nightly batch."""
    was_active = TestModeGuard.is_active()
    TestModeGuard.deactivate()
    try:
        reg, provider_registry = _mock_registry_and_provider("owlpass")
        await _seed_messages(tmp_db, "owlpass", count=3)

        coordinator = EvolutionCoordinator(
            tmp_db, provider_registry, reg, evolution_batch_size=3,
            shadow_validator=AlwaysPassShadowValidator(),
        )
        promoted = await coordinator.evolve_one_owl_now("owlpass")

        assert promoted is True
        # LLM_QUALITY signal strength scales the raw 0.02 delta by 0.3x (0.006).
        assert reg.get("owlpass").dna.curiosity == pytest.approx(0.506)
        rows = await tmp_db.fetch_all(
            "SELECT curiosity FROM owl_dna WHERE owl_name = ?", ("owlpass",)
        )
        assert len(rows) == 1
        assert rows[0]["curiosity"] == pytest.approx(0.506)
        cps = await tmp_db.fetch_all(
            "SELECT checkpoint_id FROM learning_artifacts "
            "WHERE artifact_type = 'dna' AND artifact_id = ? AND reason = 'evolve_now'",
            ("owlpass",),
        )
        assert len(cps) == 1
    finally:
        if was_active:
            TestModeGuard.activate()


@pytest.mark.asyncio
async def test_evolve_one_owl_now_gate_rejection_restores_checkpoint(tmp_db: DbPool) -> None:
    """AD-1/AD-3: a rejecting shadow gate leaves the owl's DNA exactly as it
    was pre-mutation — proving evolve_now is gated from day one, by
    construction, not a second ungated promotion path."""
    was_active = TestModeGuard.is_active()
    TestModeGuard.deactivate()
    try:
        reg, provider_registry = _mock_registry_and_provider("owlrejected")
        await _seed_messages(tmp_db, "owlrejected", count=3)

        coordinator = EvolutionCoordinator(
            tmp_db, provider_registry, reg, evolution_batch_size=3,
            shadow_validator=AlwaysFailShadowValidator(),
        )
        promoted = await coordinator.evolve_one_owl_now("owlrejected")

        assert promoted is False
        assert reg.get("owlrejected").dna.curiosity == pytest.approx(0.50)
        rows = await tmp_db.fetch_all(
            "SELECT curiosity FROM owl_dna WHERE owl_name = ?", ("owlrejected",)
        )
        assert len(rows) == 1
        assert rows[0]["curiosity"] == pytest.approx(0.50)
    finally:
        if was_active:
            TestModeGuard.activate()


@pytest.mark.asyncio
async def test_evolve_one_owl_now_no_material_returns_false(tmp_db: DbPool) -> None:
    """A brand-new owl with too little conversation history yet gets a benign
    ``False`` (existing, unchanged ``_llm_fallback`` gate — not bypassed)."""
    was_active = TestModeGuard.is_active()
    TestModeGuard.deactivate()
    try:
        reg, provider_registry = _mock_registry_and_provider("freshowl")
        # No messages seeded — excerpts=0 < batch_size, n_scored_outcomes=0.
        coordinator = EvolutionCoordinator(
            tmp_db, provider_registry, reg, evolution_batch_size=3,
            shadow_validator=AlwaysPassShadowValidator(),
        )
        promoted = await coordinator.evolve_one_owl_now("freshowl")
        assert promoted is False
        assert reg.get("freshowl").dna.curiosity == pytest.approx(0.50)
    finally:
        if was_active:
            TestModeGuard.activate()


@pytest.mark.asyncio
async def test_evolve_one_owl_now_unknown_owl_raises(tmp_db: DbPool) -> None:
    """Unknown owl name propagates OwlNotFoundError (matches this file's
    existing manifest-lookup convention, e.g. ``_dna_restore``)."""
    reg = OwlRegistry()
    provider_registry = ProviderRegistry()
    coordinator = EvolutionCoordinator(
        tmp_db, provider_registry, reg,
        shadow_validator=AlwaysPassShadowValidator(),
    )
    with pytest.raises(OwlNotFoundError):
        await coordinator.evolve_one_owl_now("ghost")
