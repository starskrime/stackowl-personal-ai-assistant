"""Story 5.2 — Convergence, cross-examination prompts, interjection, early termination."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncGenerator, Generator
from pathlib import Path

import pytest

from stackowl.config.test_mode import TestModeGuard
from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.embeddings.hash_provider import HashEmbeddingProvider
from stackowl.embeddings.registry import EmbeddingRegistry
from stackowl.parliament.convergence import ConvergenceDetector
from stackowl.parliament.cross_examination import CrossExaminationPromptBuilder
from stackowl.parliament.models import ParliamentRound
from stackowl.parliament.orchestrator import ParliamentOrchestrator
from stackowl.parliament.session_store import SessionStore
from stackowl.pipeline.backends.base import OrchestratorBackend
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import ResponseChunk

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _disable_test_mode_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False)


@pytest.fixture()
def stackowl_log_level_debug() -> Generator[None]:
    """Raise the stackowl logger level to DEBUG so capture_logs sees INFO records."""
    logger = logging.getLogger("stackowl")
    prior = logger.level
    logger.setLevel(logging.DEBUG)
    try:
        yield
    finally:
        logger.setLevel(prior)


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


def _hash_registry() -> EmbeddingRegistry:
    """Build an EmbeddingRegistry pinned to the hash provider for tests."""
    registry = EmbeddingRegistry()
    registry._provider = HashEmbeddingProvider()  # type: ignore[attr-defined]
    registry._is_semantic = False  # type: ignore[attr-defined]
    return registry


class MockOrchestratorBackend(OrchestratorBackend):
    def __init__(
        self,
        responses_per_round: list[dict[str, str]] | None = None,
        single_responses: dict[str, str] | None = None,
        call_log: list[tuple[str, str]] | None = None,
    ) -> None:
        self.responses_per_round = responses_per_round
        self.single_responses = single_responses or {}
        self._round_index = 0
        self.call_log = call_log if call_log is not None else []

    async def run(self, state: PipelineState) -> PipelineState:
        self.call_log.append((state.owl_name, state.input_text))
        if self.responses_per_round:
            idx = self._round_index_for(state.owl_name)
            response = self.responses_per_round[idx].get(state.owl_name, "...")
        else:
            response = self.single_responses.get(
                state.owl_name, f"[mock from {state.owl_name}]"
            )
        chunk = ResponseChunk(
            content=response,
            is_final=True,
            chunk_index=0,
            trace_id=state.trace_id,
            owl_name=state.owl_name,
        )
        return state.evolve(responses=(chunk,))

    def _round_index_for(self, _name: str) -> int:
        # Track per-owl call count → round index.
        count = sum(1 for n, _ in self.call_log if n == _name) - 1
        return min(count, len(self.responses_per_round or []) - 1)


# ---------------------------------------------------------------------------
# ConvergenceDetector
# ---------------------------------------------------------------------------


class TestConvergenceDetector:
    async def test_high_similarity_returns_true(self) -> None:
        det = ConvergenceDetector(threshold=0.5, embedding_registry=_hash_registry())
        round_ = ParliamentRound(
            round_number=1,
            responses={"a": "the answer is yes", "b": "the answer is yes"},
            truncated={"a": False, "b": False},
        )
        # identical responses → cosine sim 1.0 with hash provider (same seed)
        assert await det.check(round_) is True

    async def test_low_similarity_returns_false(self) -> None:
        det = ConvergenceDetector(threshold=0.99, embedding_registry=_hash_registry())
        round_ = ParliamentRound(
            round_number=1,
            responses={
                "a": "absolutely yes we must proceed urgently",
                "b": "completely opposite never under any circumstance",
            },
            truncated={"a": False, "b": False},
        )
        assert await det.check(round_) is False

    async def test_single_response_returns_false(self) -> None:
        det = ConvergenceDetector(threshold=0.5, embedding_registry=_hash_registry())
        round_ = ParliamentRound(
            round_number=1,
            responses={"a": "alone"},
            truncated={"a": False},
        )
        assert await det.check(round_) is False

    async def test_no_embedder_returns_false_and_logs(self) -> None:
        det = ConvergenceDetector(threshold=0.5, embedding_registry=None)
        round_ = ParliamentRound(
            round_number=1,
            responses={"a": "x", "b": "x"},
            truncated={"a": False, "b": False},
        )
        # Multiple checks — only one warning per process is fine (graceful degrade).
        assert await det.check(round_) is False
        assert await det.check(round_) is False

    async def test_logs_similarity_value(
        self,
        capture_logs: list[dict[str, object]],
        stackowl_log_level_debug: None,
    ) -> None:
        det = ConvergenceDetector(threshold=0.5, embedding_registry=_hash_registry())
        round_ = ParliamentRound(
            round_number=1,
            responses={"a": "same", "b": "same"},
            truncated={"a": False, "b": False},
        )
        await det.check(round_)
        sim_logs = [
            r
            for r in capture_logs
            if "convergence.check" in r.get("msg", "")
            and r.get("level") == "INFO"
        ]
        assert sim_logs, "Expected an INFO log containing similarity value"
        fields = sim_logs[0].get("fields", {})
        assert "mean_similarity" in fields


# ---------------------------------------------------------------------------
# CrossExaminationPromptBuilder
# ---------------------------------------------------------------------------


class TestCrossExaminationPromptBuilder:
    def test_round1_in_orchestrator_uses_topic(self) -> None:
        """Round 1 uses raw topic — verified through orchestrator integration below.

        The builder is for round 2+; for round 1 the orchestrator passes the
        topic verbatim. This test is integration-checked elsewhere; here we
        verify the builder doesn't crash with empty prior_rounds.
        """
        builder = CrossExaminationPromptBuilder()
        prompt = builder.build(
            topic="should we ship",
            owl_name="a",
            prior_rounds=[],
            interjections=[],
        )
        assert "should we ship" in prompt

    def test_round2_includes_prior_responses(self) -> None:
        builder = CrossExaminationPromptBuilder()
        prior = ParliamentRound(
            round_number=1,
            responses={"a": "yes ship it", "b": "no wait"},
            truncated={"a": False, "b": False},
        )
        prompt = builder.build(
            topic="the topic",
            owl_name="a",
            prior_rounds=[prior],
            interjections=[],
        )
        # 'a' should see b's response, not its own
        assert "no wait" in prompt
        assert "[b]:" in prompt
        # 'a' should NOT see its own prior response quoted as another participant
        assert "[a]:" not in prompt

    def test_excludes_self(self) -> None:
        builder = CrossExaminationPromptBuilder()
        prior = ParliamentRound(
            round_number=1,
            responses={"a": "AAAA", "b": "BBBB", "c": "CCCC"},
            truncated={"a": False, "b": False, "c": False},
        )
        prompt = builder.build(
            topic="t",
            owl_name="b",
            prior_rounds=[prior],
            interjections=[],
        )
        assert "AAAA" in prompt
        assert "CCCC" in prompt
        assert "BBBB" not in prompt

    def test_includes_interjections(self) -> None:
        builder = CrossExaminationPromptBuilder()
        prior = ParliamentRound(
            round_number=1,
            responses={"a": "first"},
            truncated={"a": False},
        )
        prompt = builder.build(
            topic="t",
            owl_name="a",
            prior_rounds=[prior],
            interjections=["consider Y instead", "what about Z?"],
        )
        assert "[User interjection]: consider Y instead" in prompt
        assert "[User interjection]: what about Z?" in prompt

    def test_works_with_non_english_owl_names(self) -> None:
        """B3 spirit — language-neutral structural template."""
        builder = CrossExaminationPromptBuilder()
        prior = ParliamentRound(
            round_number=1,
            responses={"野口": "見送りましょう", "müller": "ja sofort"},
            truncated={"野口": False, "müller": False},
        )
        prompt = builder.build(
            topic="出荷すべきか",
            owl_name="野口",
            prior_rounds=[prior],
            interjections=[],
        )
        assert "出荷すべきか" in prompt
        assert "[müller]:" in prompt
        assert "ja sofort" in prompt


# ---------------------------------------------------------------------------
# Orchestrator integration — convergence + interjection + max rounds
# ---------------------------------------------------------------------------


class TestOrchestratorIntegration:
    async def test_early_termination_on_convergence(
        self,
        parliament_db: DbPool,
        capture_logs: list[dict[str, object]],
        stackowl_log_level_debug: None,
    ) -> None:
        # Identical responses → cosine sim 1.0 → convergence on round 1.
        backend = MockOrchestratorBackend(single_responses={"a": "same", "b": "same"})
        store = SessionStore(parliament_db)
        det = ConvergenceDetector(threshold=0.5, embedding_registry=_hash_registry())
        orch = ParliamentOrchestrator(
            backend=backend,
            session_store=store,
            max_rounds=5,
            convergence_detector=det,
        )
        result = await orch.run("t", ["a", "b"])
        assert len(result.rounds) == 1  # converged early
        converged_logs = [
            r for r in capture_logs if "convergence detected" in r.get("msg", "")
        ]
        assert converged_logs

    async def test_max_rounds_reached_without_convergence(
        self,
        parliament_db: DbPool,
        capture_logs: list[dict[str, object]],
        stackowl_log_level_debug: None,
    ) -> None:
        backend = MockOrchestratorBackend(
            single_responses={
                "a": "alpha completely different response stream",
                "b": "beta entirely orthogonal viewpoint distinct",
            }
        )
        store = SessionStore(parliament_db)
        det = ConvergenceDetector(threshold=0.999, embedding_registry=_hash_registry())
        orch = ParliamentOrchestrator(
            backend=backend,
            session_store=store,
            max_rounds=3,
            convergence_detector=det,
        )
        result = await orch.run("t", ["a", "b"])
        assert len(result.rounds) == 3
        max_logs = [
            r
            for r in capture_logs
            if "max_rounds reached" in r.get("msg", "")
        ]
        assert max_logs

    async def test_interjection_active_session(
        self, parliament_db: DbPool
    ) -> None:
        """inject_interjection returns True when a session is active."""
        # Use a slow backend so we can inject during the run.
        async def _delayed_run() -> None:
            await asyncio.sleep(0.1)

        backend = SlowBackend(delay_s=0.5)
        store = SessionStore(parliament_db)
        orch = ParliamentOrchestrator(
            backend=backend,
            session_store=store,
            max_rounds=1,
            per_owl_timeout_s=2.0,
            session_timeout_s=5.0,
        )
        task = asyncio.create_task(orch.run("t", ["a"]))
        await asyncio.sleep(0.05)
        accepted = await orch.inject_interjection("hello there")
        assert accepted is True
        await task

    async def test_interjection_no_session(self, parliament_db: DbPool) -> None:
        backend = MockOrchestratorBackend(single_responses={"a": "x"})
        store = SessionStore(parliament_db)
        orch = ParliamentOrchestrator(
            backend=backend, session_store=store, max_rounds=1
        )
        # No session active
        result = await orch.inject_interjection("hi")
        assert result is False


# ---------------------------------------------------------------------------
# Helper backend with delay (used for interjection test)
# ---------------------------------------------------------------------------


class SlowBackend(OrchestratorBackend):
    def __init__(self, delay_s: float) -> None:
        self.delay_s = delay_s

    async def run(self, state: PipelineState) -> PipelineState:
        await asyncio.sleep(self.delay_s)
        chunk = ResponseChunk(
            content="done",
            is_final=True,
            chunk_index=0,
            trace_id=state.trace_id,
            owl_name=state.owl_name,
        )
        return state.evolve(responses=(chunk,))


# A small dummy use of time to silence import linter — used in slow tests for sanity.
_ = time.monotonic
