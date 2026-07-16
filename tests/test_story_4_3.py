"""Story 4.3 — DNA evolution, validation, checkpointing, prompt injection."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from stackowl.config.test_mode import TestModeGuard
from stackowl.db.pool import DbPool
from stackowl.exceptions import ManifestValidationError
from stackowl.owls.dna import OwlDNA
from stackowl.owls.dna_injector import DNAPromptInjector
from stackowl.owls.dna_storage import DNACheckpointer
from stackowl.owls.evolution import DeltaValidator, EvolutionCoordinator
from stackowl.owls.evolution_prompt import EvolutionPromptBuilder
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.providers.mock_provider import MockProvider
from stackowl.providers.registry import ProviderRegistry
from stackowl.scheduler.job import Job


def _manifest(
    name: str = "miko",
    role: str = "analyst",
    dna: OwlDNA | None = None,
) -> OwlAgentManifest:
    return OwlAgentManifest(
        name=name,
        role=role,
        system_prompt="Be helpful and accurate.",
        model_tier="fast",
        dna=dna if dna is not None else OwlDNA(),
    )


# ---------------------------------------------------------------------------
# DeltaValidator
# ---------------------------------------------------------------------------


class TestDeltaValidator:
    def test_valid_json_returns_dict(self) -> None:
        v = DeltaValidator()
        raw = (
            '{"challenge_level": 0.05, "verbosity": -0.03, "curiosity": 0.0, '
            '"formality": 0.0, "creativity": 0.0, "precision": 0.0}'
        )
        result = v.validate(raw)
        assert result["challenge_level"] == pytest.approx(0.05)
        assert result["verbosity"] == pytest.approx(-0.03)
        assert result["curiosity"] == pytest.approx(0.0)

    def test_values_clamped_to_range(self) -> None:
        # FR-1 (commit 47069e05) retuned the clamp band to ±0.25 — this test
        # was stale (still asserting the old ±0.1 band); root-caused and fixed
        # here (pre-existing failure, unrelated to Story 2.3).
        v = DeltaValidator()
        raw = '{"challenge_level": 0.5, "verbosity": -0.4}'
        result = v.validate(raw)
        assert result["challenge_level"] == pytest.approx(0.25)
        assert result["verbosity"] == pytest.approx(-0.25)

    def test_unknown_traits_skipped(self) -> None:
        v = DeltaValidator()
        raw = '{"challenge_level": 0.05, "nonsense_trait": 0.1, "decay_rate_per_week": 0.05}'
        result = v.validate(raw)
        assert result == {"challenge_level": pytest.approx(0.05)}
        assert "nonsense_trait" not in result
        assert "decay_rate_per_week" not in result

    def test_invalid_json_returns_empty(self) -> None:
        v = DeltaValidator()
        # Should not raise
        assert v.validate("not json at all{{{}") == {}

    def test_non_object_payload_returns_empty(self) -> None:
        v = DeltaValidator()
        assert v.validate("[1, 2, 3]") == {}

    def test_non_float_value_skipped(self) -> None:
        v = DeltaValidator()
        raw = '{"challenge_level": "high", "verbosity": 0.05}'
        result = v.validate(raw)
        assert "challenge_level" not in result
        assert result["verbosity"] == pytest.approx(0.05)

    def test_markdown_fence_parsed(self) -> None:
        v = DeltaValidator()
        raw = (
            "Here are the deltas:\n"
            "```json\n"
            '{"challenge_level": 0.05, "verbosity": -0.02}\n'
            "```\n"
        )
        result = v.validate(raw)
        assert result["challenge_level"] == pytest.approx(0.05)
        assert result["verbosity"] == pytest.approx(-0.02)

    def test_bare_markdown_fence_parsed(self) -> None:
        v = DeltaValidator()
        raw = '```\n{"curiosity": 0.07}\n```'
        result = v.validate(raw)
        assert result["curiosity"] == pytest.approx(0.07)


# ---------------------------------------------------------------------------
# EvolutionPromptBuilder
# ---------------------------------------------------------------------------


class TestEvolutionPromptBuilder:
    def test_build_returns_messages(self) -> None:
        builder = EvolutionPromptBuilder()
        messages = builder.build("miko", _manifest("miko"), ["user said hi", "owl said hi"])
        assert len(messages) >= 1
        roles = {m.role for m in messages}
        assert "user" in roles  # at minimum a user-role message

    def test_prompt_contains_owl_name(self) -> None:
        builder = EvolutionPromptBuilder()
        messages = builder.build("miko", _manifest("miko"), ["hello"])
        body = "\n".join(m.content for m in messages)
        assert "miko" in body

    def test_prompt_contains_trait_values(self) -> None:
        builder = EvolutionPromptBuilder()
        dna = OwlDNA(challenge_level=0.42, verbosity=0.81, curiosity=0.5)
        messages = builder.build("miko", _manifest("miko", dna=dna), ["hello"])
        body = "\n".join(m.content for m in messages)
        assert "challenge_level" in body
        assert "0.420" in body
        assert "verbosity" in body
        assert "0.810" in body

    def test_empty_excerpts_handled(self) -> None:
        builder = EvolutionPromptBuilder()
        messages = builder.build("miko", _manifest("miko"), [])
        body = "\n".join(m.content for m in messages)
        assert "no recent conversation excerpts" in body


# ---------------------------------------------------------------------------
# DNACheckpointer (DB-backed)
# ---------------------------------------------------------------------------


class TestDNACheckpointer:
    async def test_checkpoint_returns_uuid_and_inserts_row(self, tmp_db: DbPool) -> None:
        checkpointer = DNACheckpointer(tmp_db)
        dna = OwlDNA(challenge_level=0.7, verbosity=0.3)
        checkpoint_id = await checkpointer.checkpoint("miko", dna, reason="test")
        assert isinstance(checkpoint_id, str)
        # Should be UUID4 hex (32 chars)
        uuid.UUID(checkpoint_id)
        rows = await tmp_db.fetch_all(
            "SELECT owl_name, reason FROM dna_checkpoints WHERE checkpoint_id = ?",
            (checkpoint_id,),
        )
        assert len(rows) == 1
        assert rows[0]["owl_name"] == "miko"
        assert rows[0]["reason"] == "test"

    async def test_restore_returns_dna(self, tmp_db: DbPool) -> None:
        checkpointer = DNACheckpointer(tmp_db)
        original = OwlDNA(
            challenge_level=0.42,
            verbosity=0.18,
            curiosity=0.91,
            formality=0.33,
            creativity=0.66,
            precision=0.77,
        )
        checkpoint_id = await checkpointer.checkpoint("miko", original)
        restored = await checkpointer.restore("miko", checkpoint_id)
        assert restored.challenge_level == pytest.approx(0.42)
        assert restored.verbosity == pytest.approx(0.18)
        assert restored.curiosity == pytest.approx(0.91)
        assert restored.formality == pytest.approx(0.33)
        assert restored.creativity == pytest.approx(0.66)
        assert restored.precision == pytest.approx(0.77)

    async def test_restore_unknown_raises(self, tmp_db: DbPool) -> None:
        checkpointer = DNACheckpointer(tmp_db)
        with pytest.raises(ManifestValidationError):
            await checkpointer.restore("miko", "deadbeef")

    async def test_list_returns_most_recent_first(self, tmp_db: DbPool) -> None:
        checkpointer = DNACheckpointer(tmp_db)
        ids: list[str] = []
        for i in range(3):
            cid = await checkpointer.checkpoint(
                "miko",
                OwlDNA(challenge_level=0.5 + i * 0.05),
                reason=f"r{i}",
            )
            ids.append(cid)
            # Tiny gap so created_at values can differ
            await _tick()
        listed = await checkpointer.list_checkpoints("miko", limit=10)
        assert len(listed) == 3
        # Most recent first → last inserted (r2) should lead
        assert listed[0]["reason"] == "r2"
        assert listed[-1]["reason"] == "r0"


async def _tick() -> None:
    """Yield long enough for ISO-8601 timestamps to diverge."""
    import asyncio

    await asyncio.sleep(0.005)


# ---------------------------------------------------------------------------
# DNAPromptInjector
# ---------------------------------------------------------------------------


class TestDNAPromptInjector:
    def test_neutral_dna_returns_unchanged(self) -> None:
        injector = DNAPromptInjector()
        manifest = _manifest()
        result = injector.inject(manifest, OwlDNA())
        assert result == manifest.system_prompt

    def test_inject_returns_string_starting_with_system_prompt(self) -> None:
        injector = DNAPromptInjector()
        manifest = _manifest()
        result = injector.inject(manifest, OwlDNA(challenge_level=0.95))
        assert result.startswith(manifest.system_prompt)

    def test_high_challenge_level_adds_skepticism_directive(self) -> None:
        injector = DNAPromptInjector()
        manifest = _manifest()
        result = injector.inject(manifest, OwlDNA(challenge_level=0.9))
        assert "skepticism" in result.lower() or "push back" in result.lower()

    def test_low_verbosity_adds_conciseness_directive(self) -> None:
        injector = DNAPromptInjector()
        manifest = _manifest()
        result = injector.inject(manifest, OwlDNA(verbosity=0.2))
        assert "concise" in result.lower()

    def test_high_curiosity_adds_exploration_breadth_directive(self) -> None:
        # F-53 moved act-first/anti-over-clarify to the unconditional charter;
        # curiosity now governs exploration BREADTH, not a "clarifying"
        # directive. Test was stale on the old wording — root-caused and
        # fixed here (pre-existing failure, unrelated to Story 2.3).
        injector = DNAPromptInjector()
        manifest = _manifest()
        result = injector.inject(manifest, OwlDNA(curiosity=0.85))
        assert "explore the problem broadly" in result.lower()

    def test_low_formality_adds_casual_directive(self) -> None:
        injector = DNAPromptInjector()
        manifest = _manifest()
        result = injector.inject(manifest, OwlDNA(formality=0.2))
        assert "casual" in result.lower()


# ---------------------------------------------------------------------------
# EvolutionCoordinator (integration)
# ---------------------------------------------------------------------------


class TestEvolutionCoordinator:
    def test_handler_name(self, tmp_db: DbPool) -> None:
        provider_registry = ProviderRegistry()
        owl_registry = OwlRegistry.with_default_secretary()
        coordinator = EvolutionCoordinator(tmp_db, provider_registry, owl_registry)
        assert coordinator.handler_name == "evolution_batch"

    async def test_execute_no_conversation_turns_succeeds(self, tmp_db: DbPool) -> None:
        provider_registry = ProviderRegistry()
        owl_registry = OwlRegistry.with_default_secretary()
        coordinator = EvolutionCoordinator(
            tmp_db, provider_registry, owl_registry, evolution_batch_size=3
        )
        job = _job("job-empty")
        result = await coordinator.execute(job)
        assert result.success is True
        assert result.error is None
        # No mutation rows in owl_dna
        rows = await tmp_db.fetch_all("SELECT owl_name FROM owl_dna", ())
        assert rows == []

    async def test_execute_with_mock_llm_applies_mutations(self, tmp_db: DbPool) -> None:
        # Disable test mode for the LLM call inside the coordinator.
        was_active = TestModeGuard.is_active()
        TestModeGuard.deactivate()
        try:
            provider_registry = ProviderRegistry()
            mock = MockProvider(
                name="mock-fast",
                canned_text=(
                    '{"challenge_level": 0.05, "verbosity": -0.03, "curiosity": 0.0, '
                    '"formality": 0.0, "creativity": 0.0, "precision": 0.02}'
                ),
            )
            provider_registry.register_mock("mock-fast", mock, tier="fast")

            owl_registry = OwlRegistry()
            owl_registry.register(_manifest("nora", role="analyst"))

            # Seed 3 conversation turns for nora.
            await _seed_messages(tmp_db, "nora", count=3)

            coordinator = EvolutionCoordinator(
                tmp_db, provider_registry, owl_registry, evolution_batch_size=3
            )
            result = await coordinator.execute(_job("job-mut"))
            assert result.success is True

            rows = await tmp_db.fetch_all(
                "SELECT challenge_level, verbosity, precision FROM owl_dna WHERE owl_name = ?",
                ("nora",),
            )
            assert len(rows) == 1
            assert rows[0]["challenge_level"] == pytest.approx(0.55)
            assert rows[0]["verbosity"] == pytest.approx(0.47)
            assert rows[0]["precision"] == pytest.approx(0.52)

            # A checkpoint should have been written — via the unified
            # LearningArtifactStore primitive (Story 2.3), not dna_checkpoints.
            cps = await tmp_db.fetch_all(
                "SELECT checkpoint_id FROM learning_artifacts "
                "WHERE artifact_type = 'dna' AND artifact_id = ?",
                ("nora",),
            )
            assert len(cps) == 1
        finally:
            if was_active:
                TestModeGuard.activate()


# ---------------------------------------------------------------------------
# helpers
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
