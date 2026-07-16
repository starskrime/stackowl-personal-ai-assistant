"""Task 2 — evolution_strategy scales the finalized per-trait deltas."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from stackowl.config.test_mode import TestModeGuard
from stackowl.db.pool import DbPool
from stackowl.owls.dna import OwlDNA
from stackowl.owls.evolution import EvolutionCoordinator, _scale_deltas
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.providers.mock_provider import MockProvider
from stackowl.providers.registry import ProviderRegistry
from stackowl.scheduler.job import Job


def test_conservative_halves() -> None:
    assert _scale_deltas({"curiosity": 0.2}, "conservative") == {"curiosity": 0.1}


def test_experimental_doubles() -> None:
    assert _scale_deltas({"curiosity": 0.2}, "experimental") == {"curiosity": 0.4}


def test_adaptive_is_unchanged_identity() -> None:
    d = {"curiosity": 0.2}
    assert _scale_deltas(d, "adaptive") is d  # 1× → no new dict allocated


def test_unknown_strategy_is_unchanged_identity() -> None:
    d = {"curiosity": 0.2}
    assert _scale_deltas(d, "bogus") is d


# ---------------------------------------------------------------------------
# Integration test — proves evolution_strategy is actually wired into
# _evolve_one (evolution.py:312), not just that _scale_deltas works in
# isolation. Mirrors the setup in test_evolution_feedback.py.
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


async def _run_evolution(db: DbPool, owl_name: str, strategy: str) -> float:
    """Evolve one owl (fresh registry/provider) with the given evolution_strategy,
    a raw LLM-proposed curiosity delta of +0.02 (small enough that scaling stays
    under bound_dna's MAX_DELTA=0.05 rate cap for both conservative and
    experimental, so any difference we see is from the strategy scale factor,
    not the governor clamp). Returns the resulting curiosity value.
    """
    provider_registry = ProviderRegistry()
    mock = MockProvider(
        name="mock-fast",
        canned_text=(
            '{"challenge_level": 0.0, "verbosity": 0.0, "curiosity": 0.02, '
            '"formality": 0.0, "creativity": 0.0, "precision": 0.0}'
        ),
    )
    provider_registry.register_mock("mock-fast", mock, tier="fast")

    reg = OwlRegistry()
    reg.register(
        OwlAgentManifest(
            name=owl_name,
            role="analyst",
            system_prompt="Be helpful.",
            model_tier="fast",
            dna=OwlDNA(curiosity=0.50),
            evolution_strategy=strategy,  # type: ignore[arg-type]
        )
    )
    await _seed_messages(db, owl_name, count=3)

    coordinator = EvolutionCoordinator(db, provider_registry, reg, evolution_batch_size=3)
    result = await coordinator.execute(_job(f"job-strategy-{owl_name}"))
    assert result.success is True
    return reg.get(owl_name).dna.curiosity


@pytest.mark.asyncio
async def test_evolution_strategy_scales_real_mutation(tmp_db: DbPool) -> None:
    """Same attribution-silent LLM-fallback deltas, only evolution_strategy
    differs — the resulting mutated DNA must differ measurably. This exercises
    the real call path (EvolutionCoordinator.execute -> _evolve_one ->
    evolution.py:312's `_scale_deltas(deltas, manifest.evolution_strategy)`),
    not the pure helper in isolation.
    """
    was_active = TestModeGuard.is_active()
    TestModeGuard.deactivate()
    try:
        conservative_curiosity = await _run_evolution(tmp_db, "owlconservative", "conservative")
        experimental_curiosity = await _run_evolution(tmp_db, "owlexperimental", "experimental")

        conservative_delta = conservative_curiosity - 0.50
        experimental_delta = experimental_curiosity - 0.50

        # conservative: 0.02 * 0.5 = 0.01; experimental: 0.02 * 2 = 0.04, then
        # Story 2.4's bound_dna signal-strength scaling applies on top: this is
        # the LLM-fallback path (no attribution signal), so it's tagged
        # LLM_QUALITY (0.3x) — 0.01 * 0.3 = 0.003; 0.04 * 0.3 = 0.012. This is
        # Story 2.4's intended behavior change for this path (NFR-5 dev note),
        # not a regression: the attribution/VERIFIED path stays unscaled.
        assert conservative_delta == pytest.approx(0.003, abs=1e-9)
        assert experimental_delta == pytest.approx(0.012, abs=1e-9)
        assert experimental_delta > conservative_delta
    finally:
        if was_active:
            TestModeGuard.activate()
