"""DNA mutation race — the nightly batch (``_evolve_one``) and the inline
``evolve_now`` tool (``evolve_one_owl_now``) both read-modify-write the same
owl's DNA through ``_checkpoint_validate_and_promote`` with no lock and no
version column. Two concurrent evolutions on the SAME owl used to be a
lost-update: both capture the same starting ``manifest.dna`` snapshot before
either promotes, so whichever promotes last silently discards the other's
delta.

``EvolutionCoordinator._lock_for(owl_name)`` now serializes the whole
evolve-one-owl flow per owl name, and both entry points re-fetch the manifest
AFTER acquiring the lock — so a call that had to wait sees the OTHER call's
already-promoted state as its starting point, and both deltas land
cumulatively instead of one clobbering the other.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import pytest

from stackowl.config.test_mode import TestModeGuard
from stackowl.db.pool import DbPool
from stackowl.owls.dna import OwlDNA
from stackowl.owls.evolution import EvolutionCoordinator
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.providers.mock_provider import MockProvider
from stackowl.providers.registry import ProviderRegistry
from tests._story_2_6_helpers import AlwaysPassShadowValidator


async def _seed_messages(db: DbPool, owl_name: str, count: int) -> None:
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


def _mock_registry_and_provider(owl_name: str, curiosity_delta: float) -> tuple[OwlRegistry, ProviderRegistry]:
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
async def test_concurrent_evolve_now_calls_on_same_owl_apply_cumulatively(
    tmp_db: DbPool,
) -> None:
    """Two concurrent evolve_one_owl_now calls on the SAME owl must both land
    (0.50 + 0.006 + 0.006 = 0.512), not lose one to the other (which a lost
    update would leave at 0.506 — only one call's delta survives)."""
    was_active = TestModeGuard.is_active()
    TestModeGuard.deactivate()
    try:
        reg, provider_registry = _mock_registry_and_provider("racer", curiosity_delta=0.02)
        await _seed_messages(tmp_db, "racer", count=3)

        coordinator = EvolutionCoordinator(
            tmp_db, provider_registry, reg, evolution_batch_size=3,
            shadow_validator=AlwaysPassShadowValidator(),
        )

        results = await asyncio.gather(
            coordinator.evolve_one_owl_now("racer"),
            coordinator.evolve_one_owl_now("racer"),
        )

        assert results == [True, True]
        # LLM_QUALITY signal strength scales each raw 0.02 delta by 0.3x (0.006).
        assert reg.get("racer").dna.curiosity == pytest.approx(0.512)
        rows = await tmp_db.fetch_all(
            "SELECT curiosity FROM owl_dna WHERE owl_name = ?", ("racer",)
        )
        assert len(rows) == 1
        assert rows[0]["curiosity"] == pytest.approx(0.512), (
            "lost update: the persisted DNA row does not reflect both concurrent mutations"
        )
    finally:
        if was_active:
            TestModeGuard.activate()


@pytest.mark.asyncio
async def test_lock_for_returns_same_lock_instance_per_owl_name() -> None:
    reg = OwlRegistry()
    provider_registry = ProviderRegistry()
    coordinator = EvolutionCoordinator(None, provider_registry, reg)  # type: ignore[arg-type]

    lock_a = coordinator._lock_for("scout")
    lock_b = coordinator._lock_for("scout")
    lock_other = coordinator._lock_for("sage")

    assert lock_a is lock_b
    assert lock_a is not lock_other
