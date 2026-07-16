"""DNA-completion full-loop GATEWAY JOURNEY (dna-completion Task 10).

ONE integration journey driving the REAL machinery (mock ONLY the AI provider)
that proves the whole authored-anchor DNA loop closes end-to-end:

    (1) AUTHORED CAPTURE — ``capture_authored_dna`` snapshots the registry's
        authored DNA into ``owl_dna_authored``; ``read_authored_dna`` reads it
        back faithfully (challenge_level == 0.75, NOT neutral 0.5).

    (2) ANCHORED ENVELOPE — a REAL evolution batch (governor ``bound_dna``,
        persist, live-overlay all real; only the LLM is a MockProvider) lets an
        owl whose current ``challenge_level`` is ABOVE the old neutral ceiling
        (0.85) evolve UP to ~0.90 and STAY there — impossible under the old
        neutral envelope ([0.2, 0.8] would clamp it to 0.8). Proves the authored
        anchor (0.75 → band [0.45, 1.0]) is in effect through the whole pipeline.

    (3) SCHMITT LATCH — the REAL ``DNAPromptInjector`` (backed by the singleton
        ``DIRECTIVE_LATCH``) emits the high-challenge directive at 0.72, STILL
        emits it at 0.66 (deadband hold), then drops it at 0.58 (below HIGH_EXIT).

    (4) RESET — ``OwlsCommand`` ``/owls reset-dna <name> YES`` reverts the
        ``owl_dna`` row to authored, live-refreshes the registry manifest, and
        clears the latch (a subsequent cold-seed at 0.66 emits nothing).

Scaffolding reused VERBATIM from ``tests/journeys/test_persona_evolution_journey.py``
(``db`` fixture, ``_manifest``, ``_job``, ``_seed_messages``, ``_persisted_dna``,
``_run_batch`` — the REAL ``EvolutionCoordinator`` + governor + MockProvider) and
``tests/commands/test_owls_reset_dna.py`` (``_state`` PipelineState, OwlsCommand
construction). The AI provider is the ONLY mock.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator, Generator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from stackowl.commands.owls_command import OwlsCommand
from stackowl.config.test_mode import TestModeGuard
from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.owls.directive_latch import DIRECTIVE_LATCH
from stackowl.owls.dna import OwlDNA
from stackowl.owls.dna_authored import capture_authored_dna, read_authored_dna
from stackowl.owls.dna_hydrator import apply_dna_overlay
from stackowl.owls.dna_injector import DNAPromptInjector
from stackowl.owls.dna_storage import upsert_owl_dna
from stackowl.owls.evolution import EvolutionCoordinator
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.state import PipelineState
from stackowl.providers.mock_provider import MockProvider
from stackowl.providers.registry import ProviderRegistry
from stackowl.scheduler.job import Job
from tests._story_2_6_helpers import AlwaysPassShadowValidator

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Fixtures & scaffolding (reused from test_persona_evolution_journey.py)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_latch() -> Generator[None]:
    """Clear the DIRECTIVE_LATCH singleton around every test (no leakage)."""
    DIRECTIVE_LATCH.clear_all()
    yield
    DIRECTIVE_LATCH.clear_all()


@pytest.fixture()
async def db(tmp_path: Path) -> AsyncGenerator[DbPool]:
    db_path = tmp_path / "dna_completion_journey.db"
    MigrationRunner(db_path=db_path).run()
    pool = DbPool(db_path=db_path)
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


def _manifest(name: str, dna: OwlDNA | None = None) -> OwlAgentManifest:
    return OwlAgentManifest(
        name=name,
        role="analyst",
        system_prompt="Be helpful and accurate.",
        model_tier="fast",
        dna=dna if dna is not None else OwlDNA(),
    )


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
        (conv_id, f"sess-{owl_name}-{conv_id[:6]}", owl_name, now, count),
    )
    for i in range(count):
        await db.execute(
            "INSERT INTO messages (id, conversation_id, role, content, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (uuid.uuid4().hex, conv_id, "user", f"sample message {i}", now),
        )


async def _persisted_dna(db: DbPool, owl_name: str) -> dict[str, float]:
    rows = await db.fetch_all(
        "SELECT challenge_level, verbosity, curiosity, formality, creativity, "
        "precision FROM owl_dna WHERE owl_name = ?",
        (owl_name,),
    )
    assert rows, f"no persisted owl_dna row for {owl_name}"
    return {k: float(v) for k, v in rows[0].items()}


async def _run_batch(
    db: DbPool, owl_registry: OwlRegistry, deltas_json: str, *, job_id: str
) -> None:
    """Run ONE REAL evolution batch with a mocked LLM returning ``deltas_json``.

    The governor (bound_dna, reading the authored anchor), persist, and
    live-overlay are ALL real — only the provider is a MockProvider.
    """
    provider_registry = ProviderRegistry()
    provider_registry.register_mock(
        "mock-fast", MockProvider(name="mock-fast", canned_text=deltas_json), tier="fast"
    )
    # Story 2.6 — no task_outcomes seeded (cold start), so stub the gate: this
    # journey is about the DNA-completion path, not gate mechanics.
    coordinator = EvolutionCoordinator(
        db, provider_registry, owl_registry, evolution_batch_size=3,
        shadow_validator=AlwaysPassShadowValidator(),
    )
    was_active = TestModeGuard.is_active()
    TestModeGuard.deactivate()
    try:
        result = await coordinator.execute(_job(job_id))
    finally:
        if was_active:
            TestModeGuard.activate()
    assert result.success is True, result.error


def _state(owl_name: str) -> PipelineState:
    """PipelineState shaped like the OwlsCommand tests build it."""
    return PipelineState(
        trace_id="trace-dna-journey",
        session_id="sess-dna-journey",
        input_text="hello",
        channel="cli",
        owl_name=owl_name,
        pipeline_step="receive",
    )


# ---------------------------------------------------------------------------
# Task 10 — full-loop DNA-completion journey
# ---------------------------------------------------------------------------


async def test_dna_completion_full_loop(db: DbPool) -> None:
    owl = "sentinel"

    # --- setup: registry with an owl AUTHORED at challenge_level 0.75 (a
    # boundary-adjacent, distinctly-non-neutral value). Seed enough messages
    # that the coordinator's LLM-fallback excerpt gate (>= batch_size=3) opens.
    registry = OwlRegistry()
    registry.register(_manifest(owl, dna=OwlDNA(challenge_level=0.75)))
    await _seed_messages(db, owl, count=3)

    # =====================================================================
    # (1) AUTHORED CAPTURE — snapshot the registry's authored DNA, read back.
    # =====================================================================
    captured = await capture_authored_dna(registry, db)
    assert captured == 1
    authored = await read_authored_dna(db, owl)
    assert authored is not None
    assert authored.challenge_level == pytest.approx(0.75)  # NOT neutral 0.5

    # =====================================================================
    # (2) ANCHORED ENVELOPE — current 0.85 (ABOVE the old neutral ceiling
    #     0.8); a real batch proposing a further +challenge_level evolves it
    #     UP to ~0.90 and it STAYS — only possible because the authored anchor
    #     (0.75 → band [0.45, 1.0]) is in effect end-to-end. Under the old
    #     neutral envelope ([0.2, 0.8]) the value would be clamped DOWN to 0.8.
    # =====================================================================
    await upsert_owl_dna(db, owl, OwlDNA(challenge_level=0.85), table="owl_dna")
    apply_dna_overlay(registry, owl, OwlDNA(challenge_level=0.85))
    assert registry.get(owl).dna.challenge_level == pytest.approx(0.85)

    # LLM proposes a big +challenge_level. Validator clamps the delta to +0.1,
    # mutate applies it (0.85 → 0.95), then the governor rate-caps to +0.05
    # → 0.90, clamped into the AUTHORED band [0.45, 1.0] (i.e. NOT clamped).
    await _run_batch(
        db,
        registry,
        '{"challenge_level": 0.5, "verbosity": 0.0, "curiosity": 0.0, '
        '"formality": 0.0, "creativity": 0.0, "precision": 0.0}',
        job_id="job-evolve-up",
    )

    live_cl = registry.get(owl).dna.challenge_level
    persisted_cl = (await _persisted_dna(db, owl))["challenge_level"]
    # The DISTINGUISHER: > 0.8 is impossible under the old neutral envelope.
    assert live_cl > 0.8, f"anchor not in effect — clamped to neutral ceiling: {live_cl}"
    assert live_cl == pytest.approx(0.90)
    assert persisted_cl == pytest.approx(0.90)  # DB == live (persist is source of truth)

    # =====================================================================
    # (3) SCHMITT LATCH — the REAL injector emits / holds / drops the
    #     high-challenge directive across the hysteresis band. Same owl name so
    #     the singleton latch accumulates state turn-to-turn.
    # =====================================================================
    DIRECTIVE_LATCH.clear_all()
    inj = DNAPromptInjector()
    high_directive = "push back on weak arguments"  # from the challenge_level HIGH directive

    # 0.72 >= HIGH_ENTER (0.62) → latch ON, directive present.
    out_enter = inj.inject(_manifest(owl), OwlDNA(challenge_level=0.72))
    assert high_directive in out_enter.lower()

    # 0.58 in deadband [0.55, 0.62) → latch HOLDS, directive STILL present.
    out_hold = inj.inject(_manifest(owl), OwlDNA(challenge_level=0.58))
    assert high_directive in out_hold.lower()

    # 0.50 < HIGH_EXIT (0.55) → latch OFF, directive gone.
    out_exit = inj.inject(_manifest(owl), OwlDNA(challenge_level=0.50))
    assert high_directive not in out_exit.lower()

    # =====================================================================
    # (4) RESET via the command — evolved 0.8 + latch ON; reset-dna reverts to
    #     authored (0.75), live-refreshes the registry, and clears the latch.
    # =====================================================================
    await upsert_owl_dna(db, owl, OwlDNA(challenge_level=0.8), table="owl_dna")
    apply_dna_overlay(registry, owl, OwlDNA(challenge_level=0.8))
    DIRECTIVE_LATCH.clear_all()
    DIRECTIVE_LATCH.high_state(owl, "challenge_level", 0.80)  # latch ON
    assert DIRECTIVE_LATCH.high_state(owl, "challenge_level", 0.66) is True  # still >= HIGH_ENTER (0.62)

    cmd = OwlsCommand(owl_registry=registry, db=db, event_bus=None, tool_registry=None)
    out = await cmd.handle(f"reset-dna {owl} YES", _state(owl))
    assert "reset" in out.lower()

    # owl_dna row reverted to authored 0.75.
    assert (await _persisted_dna(db, owl))["challenge_level"] == pytest.approx(0.75)
    # Live registry manifest refreshed (apply_dna_overlay ran inside reset).
    assert registry.get(owl).dna.challenge_level == pytest.approx(0.75)
    # Latch cleared: a cold-seed at 0.50 (< HIGH_ENTER 0.62) seeds OFF → no directive.
    assert DIRECTIVE_LATCH.high_state(owl, "challenge_level", 0.50) is False
