"""Task 4 — EvolutionCoordinator live-refresh + bound_dna governor.

Headline new assertions over test_story_4_3:
  (1) reg.get(owl).dna is updated LIVE after evolution (was only checking SQLite).
  (2) A big proposed delta (here +0.5 curiosity) is capped to MAX_DELTA (0.05).
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from stackowl.config.test_mode import TestModeGuard
from stackowl.db.pool import DbPool
from stackowl.owls.dna import OwlDNA
from stackowl.owls.evolution import EvolutionCoordinator
from stackowl.owls.evolution_limits import MAX_DELTA
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.providers.mock_provider import MockProvider
from stackowl.providers.registry import ProviderRegistry
from stackowl.scheduler.job import Job


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

        coordinator = EvolutionCoordinator(
            tmp_db, provider_registry, reg, evolution_batch_size=3
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
    finally:
        if was_active:
            TestModeGuard.activate()
