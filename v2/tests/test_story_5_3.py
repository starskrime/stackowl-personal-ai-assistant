"""Story 5.3 — Synthesis, epistemic confidence, knowledge pellet generation."""

from __future__ import annotations

import time
from collections.abc import AsyncGenerator, AsyncIterator
from pathlib import Path
from typing import Literal
from unittest.mock import AsyncMock

import pytest

from stackowl.config.test_mode import TestModeGuard
from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.parliament.convergence import ConvergenceDetector
from stackowl.parliament.models import ParliamentRound, ParliamentSession
from stackowl.parliament.orchestrator import ParliamentOrchestrator
from stackowl.parliament.pellet_generator import (
    KnowledgePelletGenerator,
    MemoryBridge,
    NullMemoryBridge,
)
from stackowl.parliament.session_store import SessionStore
from stackowl.parliament.synthesis_models import (
    DisagreementPoint,
    SynthesisResult,
)
from stackowl.parliament.synthesizer import ParliamentSynthesizer
from stackowl.pipeline.backends.base import OrchestratorBackend
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import ResponseChunk
from stackowl.providers.base import CompletionResult, Message, ModelProvider

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _disable_test_mode_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable the live-I/O guard for unit tests."""
    monkeypatch.setattr(TestModeGuard, "_active", False)


@pytest.fixture()
async def parliament_db(tmp_path: Path) -> AsyncGenerator[DbPool]:
    db_path = tmp_path / "parliament.db"
    MigrationRunner(db_path=db_path).run()
    pool = DbPool(db_path=db_path)
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


# ---------------------------------------------------------------------------
# Mock provider / registry
# ---------------------------------------------------------------------------


class MockProvider(ModelProvider):
    """Deterministic provider returning canned synthesis content."""

    def __init__(self, content: str | None = None) -> None:
        self._content = content or (
            "CONSENSUS: We agree X is the best path.\n"
            "RECOMMENDATION: Proceed with X next sprint.\n"
            "DISAGREEMENT: scope | a: minimal | b: full\n"
            "◆"
        )
        self.calls: list[list[Message]] = []
        self._tier_observed: str | None = None

    @property
    def name(self) -> str:
        return "mock-powerful"

    @property
    def protocol(self) -> Literal["openai", "anthropic", "gemini"]:
        return "openai"

    async def complete(
        self,
        messages: list[Message],
        model: str,
        **kwargs: object,
    ) -> CompletionResult:
        self.calls.append(messages)
        return CompletionResult(
            content=self._content,
            input_tokens=100,
            output_tokens=50,
            model=model or "mock-model",
            provider_name=self.name,
            duration_ms=1.0,
        )

    def stream(
        self,
        messages: list[Message],
        model: str,
        **kwargs: object,
    ) -> AsyncIterator[str]:
        async def _empty() -> AsyncIterator[str]:
            for _ in ():
                yield ""

        return _empty()


class FailingProvider(ModelProvider):
    """Powerful-tier provider whose ``complete()`` always raises (FF-E8-S2-1).

    Models a synthesis-provider outage so we can assert the failure is SURFACED
    (synthesizer raises; orchestrator stores no verdict + no pellet), never
    masked as a clean confidence-scored synthesis.
    """

    def __init__(self) -> None:
        self.calls: list[list[Message]] = []

    @property
    def name(self) -> str:
        return "failing-powerful"

    @property
    def protocol(self) -> Literal["openai", "anthropic", "gemini"]:
        return "openai"

    async def complete(
        self,
        messages: list[Message],
        model: str,
        **kwargs: object,
    ) -> CompletionResult:
        self.calls.append(messages)
        raise RuntimeError("synthesis provider offline")

    def stream(
        self,
        messages: list[Message],
        model: str,
        **kwargs: object,
    ) -> AsyncIterator[str]:
        async def _empty() -> AsyncIterator[str]:
            for _ in ():
                yield ""

        return _empty()


class MockProviderRegistry:
    """Minimal provider registry that records tier requests."""

    def __init__(self, provider: ModelProvider) -> None:
        self._provider = provider
        self.tier_calls: list[str] = []

    def get_by_tier(self, tier: str) -> ModelProvider:
        self.tier_calls.append(tier)
        return self._provider

    def resolve_capable_or_degrade(self, tier: str) -> tuple[ModelProvider, str | None]:
        # The mock always serves the requested tier exactly (not degraded).
        self.tier_calls.append(tier)
        return self._provider, None


def _make_session_with_rounds(
    *,
    round_count: int = 2,
    owl_names: tuple[str, ...] = ("a", "b"),
    truncated_owls: tuple[str, ...] = (),
) -> ParliamentSession:
    """Build a completed-shape session with deterministic rounds."""
    session = ParliamentSession(topic="should we ship", owl_names=list(owl_names))
    for round_number in range(1, round_count + 1):
        responses = {n: f"resp from {n} in round {round_number}" for n in owl_names}
        truncated = {n: (n in truncated_owls) for n in owl_names}
        session = session.add_round(
            ParliamentRound(
                round_number=round_number,
                responses=responses,
                truncated=truncated,
            )
        )
    return session


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class TestSynthesisModels:
    def test_synthesis_result_frozen(self) -> None:
        result = SynthesisResult(
            consensus="agree",
            disagreements=[],
            recommendation="ship",
            confidence=0.8,
            synthesis_text="full",
        )
        with pytest.raises(Exception):
            # frozen=True → mutation must raise
            result.consensus = "changed"  # type: ignore[misc]
        with pytest.raises(Exception):
            DisagreementPoint(
                claim="x", positions={"a": "y"}, extra_field="nope"  # type: ignore[call-arg]
            )

    def test_synthesis_result_confidence_bounded(self) -> None:
        with pytest.raises(Exception):
            SynthesisResult(
                consensus="x",
                disagreements=[],
                recommendation="y",
                confidence=1.5,
                synthesis_text="z",
            )


# ---------------------------------------------------------------------------
# Synthesizer — confidence math
# ---------------------------------------------------------------------------


class TestSynthesizerConfidence:
    async def test_high_similarity_base_confidence(self) -> None:
        provider = MockProvider()
        registry = MockProviderRegistry(provider)
        det = ConvergenceDetector(embedding_registry=None)
        det.mean_similarity = AsyncMock(return_value=0.9)  # type: ignore[method-assign]
        syn = ParliamentSynthesizer(registry, convergence_detector=det)  # type: ignore[arg-type]
        session = _make_session_with_rounds(round_count=1)
        result = await syn.synthesize(session)
        assert 0.85 <= result.confidence <= 0.95

    async def test_confidence_penalized_when_majority_truncated(self) -> None:
        provider = MockProvider()
        registry = MockProviderRegistry(provider)
        det = ConvergenceDetector(embedding_registry=None)
        # PARL-1 (F078): the mock would only be reached with >=2 GENUINE
        # responses. With every owl truncated there are 0 genuine positions, so
        # _compute_mean_similarity correctly returns 0.0 WITHOUT embedding the
        # sentinels (the bug fix). mean_sim=0.0 → no-embedder base 0.7, and the
        # >50%-truncated penalty still fires: 0.7 - 0.2 = 0.5.
        det.mean_similarity = AsyncMock(return_value=0.9)  # type: ignore[method-assign]
        syn = ParliamentSynthesizer(registry, convergence_detector=det)  # type: ignore[arg-type]
        # All responses truncated → ratio = 1.0 > 0.5 → -0.2 penalty
        session = _make_session_with_rounds(
            round_count=1, owl_names=("a", "b"), truncated_owls=("a", "b")
        )
        result = await syn.synthesize(session)
        # 0.7 base (no genuine pair to measure) - 0.2 truncation penalty = 0.5
        assert 0.45 <= result.confidence <= 0.55
        # The mock was NOT reached — sentinels were excluded before embedding.
        det.mean_similarity.assert_not_awaited()

    async def test_confidence_clamped_to_zero(self) -> None:
        provider = MockProvider()
        registry = MockProviderRegistry(provider)
        det = ConvergenceDetector(embedding_registry=None)
        det.mean_similarity = AsyncMock(return_value=0.05)  # type: ignore[method-assign]
        syn = ParliamentSynthesizer(registry, convergence_detector=det)  # type: ignore[arg-type]
        session = _make_session_with_rounds(
            round_count=1, truncated_owls=("a", "b")
        )
        result = await syn.synthesize(session)
        assert 0.0 <= result.confidence <= 1.0

    async def test_no_embedder_uses_default_base(self) -> None:
        # mean_sim == 0.0 (no embedder) → base = 0.7
        provider = MockProvider()
        registry = MockProviderRegistry(provider)
        det = ConvergenceDetector(embedding_registry=None)
        syn = ParliamentSynthesizer(registry, convergence_detector=det)  # type: ignore[arg-type]
        session = _make_session_with_rounds(round_count=1)
        result = await syn.synthesize(session)
        # No truncation → base 0.7 preserved
        assert abs(result.confidence - 0.7) < 0.01


# ---------------------------------------------------------------------------
# Synthesizer — output formatting
# ---------------------------------------------------------------------------


class TestSynthesizerFormatting:
    async def test_low_confidence_warning_prepended(self) -> None:
        provider = MockProvider()
        registry = MockProviderRegistry(provider)
        det = ConvergenceDetector(embedding_registry=None)
        det.mean_similarity = AsyncMock(return_value=0.3)  # type: ignore[method-assign]
        syn = ParliamentSynthesizer(registry, convergence_detector=det)  # type: ignore[arg-type]
        session = _make_session_with_rounds(
            round_count=1, truncated_owls=("a", "b")
        )
        result = await syn.synthesize(session)
        assert result.confidence < 0.6
        assert "⚠" in result.synthesis_text
        assert "Low confidence" in result.synthesis_text

    async def test_output_contains_rollcall(self) -> None:
        provider = MockProvider()
        registry = MockProviderRegistry(provider)
        syn = ParliamentSynthesizer(registry)  # type: ignore[arg-type]
        session = _make_session_with_rounds(round_count=1, owl_names=("alpha", "beta"))
        result = await syn.synthesize(session)
        assert "Parliament:" in result.synthesis_text
        assert "alpha" in result.synthesis_text
        assert "beta" in result.synthesis_text

    async def test_output_terminates_with_diamond(self) -> None:
        provider = MockProvider()
        registry = MockProviderRegistry(provider)
        syn = ParliamentSynthesizer(registry)  # type: ignore[arg-type]
        session = _make_session_with_rounds(round_count=1)
        result = await syn.synthesize(session)
        assert result.synthesis_text.endswith("◆")

    async def test_uses_powerful_tier(self) -> None:
        provider = MockProvider()
        registry = MockProviderRegistry(provider)
        syn = ParliamentSynthesizer(registry)  # type: ignore[arg-type]
        session = _make_session_with_rounds(round_count=1)
        await syn.synthesize(session)
        assert "powerful" in registry.tier_calls


# ---------------------------------------------------------------------------
# Synthesizer — parsing edge cases
# ---------------------------------------------------------------------------


class TestSynthesizerParsing:
    async def test_parse_failure_falls_back_gracefully(self) -> None:
        # Provider returns malformed text with no markers — must not raise.
        provider = MockProvider(content="garbage random text without markers")
        registry = MockProviderRegistry(provider)
        syn = ParliamentSynthesizer(registry)  # type: ignore[arg-type]
        session = _make_session_with_rounds(round_count=1)
        result = await syn.synthesize(session)
        assert result.consensus  # populated from raw text
        assert result.recommendation == "See synthesis above"
        assert result.synthesis_text.endswith("◆")

    async def test_parses_disagreement_positions(self) -> None:
        provider = MockProvider()
        registry = MockProviderRegistry(provider)
        syn = ParliamentSynthesizer(registry)  # type: ignore[arg-type]
        session = _make_session_with_rounds(round_count=1)
        result = await syn.synthesize(session)
        assert len(result.disagreements) == 1
        d = result.disagreements[0]
        assert d.claim == "scope"
        assert d.positions == {"a": "minimal", "b": "full"}


# ---------------------------------------------------------------------------
# Synthesizer — test-mode guard
# ---------------------------------------------------------------------------


class TestSynthesizerTestMode:
    async def test_testmode_guard_blocks_call(self) -> None:
        from stackowl.config.test_mode import TestModeViolation

        TestModeGuard._active = True  # type: ignore[attr-defined]
        try:
            provider = MockProvider()
            registry = MockProviderRegistry(provider)
            syn = ParliamentSynthesizer(registry)  # type: ignore[arg-type]
            session = _make_session_with_rounds(round_count=1)
            with pytest.raises(TestModeViolation):
                await syn.synthesize(session)
        finally:
            TestModeGuard._active = False  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# PelletGenerator
# ---------------------------------------------------------------------------


class _CapturingBridge(MemoryBridge):
    def __init__(self) -> None:
        self.staged: list[tuple[str, str, str]] = []

    async def stage(
        self,
        fact_content: str,
        source_type: str,
        source_ref: str,
    ) -> None:
        self.staged.append((fact_content, source_type, source_ref))


class _FailingBridge(MemoryBridge):
    def __init__(self) -> None:
        self.attempts = 0

    async def stage(
        self,
        fact_content: str,
        source_type: str,
        source_ref: str,
    ) -> None:
        self.attempts += 1
        raise RuntimeError("memory subsystem offline")


class TestPelletGenerator:
    async def test_null_bridge_logs_info(
        self,
        capture_logs: list[dict[str, object]],
    ) -> None:
        import logging

        logging.getLogger("stackowl").setLevel(logging.DEBUG)
        bridge = NullMemoryBridge()
        await bridge.stage("a fact", "parliament", "session-1")
        infos = [
            r
            for r in capture_logs
            if r.get("level") == "INFO" and "null bridge" in r.get("msg", "")
        ]
        assert infos

    async def test_stages_consensus_and_disagreements(self) -> None:
        bridge = _CapturingBridge()
        gen = KnowledgePelletGenerator(memory_bridge=bridge)
        session = _make_session_with_rounds(round_count=1)
        synthesis = SynthesisResult(
            consensus="we agree X",
            disagreements=[
                DisagreementPoint(claim="scope dispute", positions={"a": "min", "b": "max"}),
                DisagreementPoint(claim="deadline", positions={"a": "fri", "b": "mon"}),
            ],
            recommendation="ship",
            confidence=0.85,
            synthesis_text="full",
        )
        await gen.from_parliament(session, synthesis)
        # consensus + 2 disagreement claims = 3 staged
        assert len(bridge.staged) == 3
        contents = [s[0] for s in bridge.staged]
        assert "we agree X" in contents
        assert "scope dispute" in contents
        assert "deadline" in contents
        # All carry source_type=parliament and the session id
        for _content, source_type, source_ref in bridge.staged:
            assert source_type == "parliament"
            assert source_ref == session.session_id

    async def test_bridge_failure_continues(
        self,
        capture_logs: list[dict[str, object]],
    ) -> None:
        bridge = _FailingBridge()
        gen = KnowledgePelletGenerator(memory_bridge=bridge)
        session = _make_session_with_rounds(round_count=1)
        synthesis = SynthesisResult(
            consensus="A",
            disagreements=[
                DisagreementPoint(claim="B", positions={"x": "1"}),
                DisagreementPoint(claim="C", positions={"y": "2"}),
            ],
            recommendation="r",
            confidence=0.7,
            synthesis_text="t",
        )
        # Must not raise — failures are swallowed + logged
        await gen.from_parliament(session, synthesis)
        # Bridge attempted all 3 claims despite the failures
        assert bridge.attempts == 3
        warnings = [r for r in capture_logs if r.get("level") == "WARNING"]
        assert any("bridge.stage failed" in r.get("msg", "") for r in warnings)


# ---------------------------------------------------------------------------
# Orchestrator integration
# ---------------------------------------------------------------------------


class _RecordingBackend(OrchestratorBackend):
    def __init__(self, response: str = "ok") -> None:
        self._response = response

    async def run(self, state: PipelineState) -> PipelineState:
        chunk = ResponseChunk(
            content=self._response,
            is_final=True,
            chunk_index=0,
            trace_id=state.trace_id,
            owl_name=state.owl_name,
        )
        return state.evolve(responses=(chunk,))


class TestOrchestratorWiring:
    async def test_orchestrator_runs_synthesizer_when_wired(
        self,
        parliament_db: DbPool,
    ) -> None:
        provider = MockProvider()
        registry = MockProviderRegistry(provider)
        synth = ParliamentSynthesizer(registry)  # type: ignore[arg-type]
        bridge = _CapturingBridge()
        pellet_gen = KnowledgePelletGenerator(memory_bridge=bridge)
        store = SessionStore(parliament_db)
        backend = _RecordingBackend("agreed")
        orch = ParliamentOrchestrator(
            backend=backend,
            session_store=store,
            max_rounds=1,
            synthesizer=synth,
            pellet_generator=pellet_gen,
        )
        result = await orch.run("topic", ["a", "b"])
        assert result.status == "completed"
        assert result.synthesis is not None
        assert "Parliament:" in result.synthesis
        # Pellet generator was invoked: bridge received at least the consensus
        assert len(bridge.staged) >= 1
        # Provider was called via tier=powerful
        assert "powerful" in registry.tier_calls

    async def test_orchestrator_works_without_synthesizer(
        self,
        parliament_db: DbPool,
    ) -> None:
        # Backwards-compatible: no synthesizer wired → no synthesis stored.
        store = SessionStore(parliament_db)
        backend = _RecordingBackend("text")
        orch = ParliamentOrchestrator(
            backend=backend,
            session_store=store,
            max_rounds=1,
        )
        result = await orch.run("topic", ["a", "b"])
        assert result.status == "completed"
        assert result.synthesis is None

    async def test_synthesizer_raises_on_provider_failure(self) -> None:
        # FF-E8-S2-1: a synth-provider failure must SURFACE (raise) — never be
        # masked as a clean, confidence-scored verdict built off a placeholder.
        registry = MockProviderRegistry(FailingProvider())
        synth = ParliamentSynthesizer(registry)  # type: ignore[arg-type]
        session = _make_session_with_rounds(round_count=1)
        with pytest.raises(RuntimeError, match="synthesis provider offline"):
            await synth.synthesize(session)

    async def test_orchestrator_surfaces_failed_synthesis_no_pellet(
        self,
        parliament_db: DbPool,
        capture_logs: list[dict[str, object]],
    ) -> None:
        # FF-E8-S2-1: when synthesis fails, the session completes WITHOUT a
        # synthesis (no fabricated verdict), NO pellet is staged, and the failure
        # is ERROR-logged — never a successful-looking confidence-scored output.
        provider = FailingProvider()
        registry = MockProviderRegistry(provider)
        synth = ParliamentSynthesizer(registry)  # type: ignore[arg-type]
        bridge = _CapturingBridge()
        pellet_gen = KnowledgePelletGenerator(memory_bridge=bridge)
        store = SessionStore(parliament_db)
        backend = _RecordingBackend("agreed")
        orch = ParliamentOrchestrator(
            backend=backend,
            session_store=store,
            max_rounds=1,
            synthesizer=synth,
            pellet_generator=pellet_gen,
        )
        result = await orch.run("topic", ["a", "b"])
        # Session is finished but carries NO fabricated synthesis verdict.
        assert result.status == "completed"
        assert result.synthesis is None
        # A failed synthesis yields NO stored pellet.
        assert bridge.staged == []
        # The synth provider WAS attempted (the failure path was exercised).
        assert provider.calls
        # The failure is surfaced at ERROR level, not masked / warned.
        errors = [r for r in capture_logs if r.get("level") == "ERROR"]
        assert any("synthesis failed" in r.get("msg", "") for r in errors)


# Silence import linter for time (we use it transitively via backends).
_ = time.monotonic
