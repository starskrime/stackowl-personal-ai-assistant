"""ShadowValidator tests (Story 2.5) — replay-and-score core, fully isolated.

Mirrors tests/pipeline/test_plan_a_gateway_integration.py's fake-provider pattern
(_RecordingProvider), but the fake here (_ScriptedProvider) returns DIFFERENT
canned responses per call so both an "all pass" and "one regresses" scenario can
be constructed deterministically without a real network LLM.
"""

from __future__ import annotations

import json
import time

import pytest

from stackowl.db.pool import DbPool
from stackowl.memory.outcome_store import TaskOutcomeStore
from stackowl.owls.dna import OwlDNA
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.owls.shadow_validator import ShadowValidator
from stackowl.providers.base import CompletionResult, Message, ModelProvider
from stackowl.providers.registry import ProviderRegistry

pytestmark = pytest.mark.asyncio

_OWL_NAME = "shadow_test_owl"


# ---- Fake provider — scripted per-call responses ----------------------------


class _ScriptedProvider(ModelProvider):
    """Returns the Nth scripted stream text / critic score on the Nth call.

    ``stream_texts[i]`` is what the replayed turn's own answer looks like;
    ``critic_scores[i]`` is what the SAME replay's critic call scores it. Index i
    advances in lockstep across both lists — each replay makes exactly one
    ``stream()`` call (the turn) and exactly one ``complete()`` call (the critic).
    """

    def __init__(self, stream_texts: list[str], critic_scores: list[float]) -> None:
        self._name = "scripted"
        self._stream_texts = stream_texts
        self._critic_scores = critic_scores
        self.stream_calls = 0
        self.complete_calls = 0

    @property
    def name(self) -> str:
        return self._name

    @property
    def protocol(self) -> str:  # type: ignore[override]
        return "openai"

    async def complete(
        self, messages: list[Message], model: str, **kwargs: object
    ) -> CompletionResult:
        idx = self.complete_calls
        self.complete_calls += 1
        score = self._critic_scores[idx] if idx < len(self._critic_scores) else 0.9
        return CompletionResult(
            content=json.dumps({"score": score, "reason": "stub"}),
            input_tokens=5, output_tokens=5, model="stub",
            provider_name=self._name, duration_ms=1.0,
        )

    async def stream(  # type: ignore[override]
        self, messages: list[Message], model: str, **kwargs: object
    ):
        idx = self.stream_calls
        self.stream_calls += 1
        text = self._stream_texts[idx] if idx < len(self._stream_texts) else "ok"
        yield text


# ---- Helpers ------------------------------------------------------------


def _manifest(dna: OwlDNA | None = None) -> OwlAgentManifest:
    return OwlAgentManifest(
        name=_OWL_NAME,
        role="test-owl",
        system_prompt="You are a test owl.",
        model_tier="standard",
        dna=dna or OwlDNA(),
    )


def _build_registry_provider(stream_texts: list[str], critic_scores: list[float]) -> tuple[
    OwlRegistry, ProviderRegistry, _ScriptedProvider,
]:
    live_registry = OwlRegistry()
    live_registry.register(_manifest())
    preg = ProviderRegistry()
    provider = _ScriptedProvider(stream_texts, critic_scores)
    # Owl-named binding wins provider_select's precedence (Step 0) — covers the
    # replay's own turn (stream()). Also registered under "fast" tier for the
    # critic call (ShadowValidator.get_with_cascade("fast")).
    preg.register_mock(_OWL_NAME, provider, tier="standard")
    preg.register_mock("fast", provider, tier="fast")
    return live_registry, preg, provider


async def _seed_outcomes(
    db: DbPool, *, n: int, base_trace: str = "seed",
) -> None:
    """Seed N trustworthy, scored task_outcomes rows for _OWL_NAME, oldest→newest
    (so captured_at ordering matches insertion order; list_scored_for_owl reads
    newest-first, i.e. reverse of insertion order)."""
    store = TaskOutcomeStore(db)
    for i in range(n):
        trace_id = f"{base_trace}-{i}"
        await store.record(
            trace_id=trace_id,
            session_id="seed-session",
            owl_name=_OWL_NAME,
            channel="cli",
            success=True,
            latency_ms=100.0,
            tool_call_count=0,
            failure_class=None,
            step_durations={},
            input_text=f"input {i}",
            response_text=f"prior response {i}",
        )
        row = await store.get_by_trace_id(trace_id)
        assert row is not None
        await store.set_quality_score(row.outcome_id, 0.9)
        # Force distinct, increasing captured_at so DESC ordering is deterministic
        # even when seeding runs faster than sqlite's time resolution.
        await db.execute(
            "UPDATE task_outcomes SET captured_at = ? WHERE trace_id = ?",
            (time.time() + i, trace_id),
        )


# ---- Tests ----------------------------------------------------------------


async def test_happy_path_n_consecutive_passes(tmp_db: DbPool) -> None:
    """N (default 3) consecutive trustworthy replays -> passed=True."""
    await _seed_outcomes(tmp_db, n=5)
    _live_registry, preg, provider = _build_registry_provider(
        stream_texts=["great answer"] * 5,
        critic_scores=[0.9, 0.9, 0.9, 0.9, 0.9],
    )
    validator = ShadowValidator(tmp_db, preg)

    result = await validator.validate(_OWL_NAME, _manifest(), OwlDNA())

    assert result.passed is True
    assert result.consecutive_non_regressions == 3
    assert result.n_replayed == 3  # stopped early once threshold was met
    assert result.failures == ()
    assert provider.stream_calls == 3
    assert provider.complete_calls == 3


async def test_regression_breaks_streak_not_rescued_later(tmp_db: DbPool) -> None:
    """A low-scoring replay breaks the streak; a LATER trustworthy replay in the
    sample does not rescue it — consecutive means unbroken from the start."""
    await _seed_outcomes(tmp_db, n=5)
    # Held-out sample is newest-first; replay order: idx0 (newest) .. idx4 (oldest).
    # idx0, idx1 pass; idx2 regresses (score < 0.6); idx3, idx4 would pass but must
    # never be reached / never rescue the streak.
    _live_registry, preg, provider = _build_registry_provider(
        stream_texts=["ok"] * 5,
        critic_scores=[0.9, 0.9, 0.2, 0.9, 0.9],
    )
    validator = ShadowValidator(tmp_db, preg)

    result = await validator.validate(_OWL_NAME, _manifest(), OwlDNA())

    assert result.passed is False
    assert result.consecutive_non_regressions == 2
    assert result.n_replayed == 3  # stopped at the regression — idx3/idx4 never run
    assert len(result.failures) == 1
    assert provider.stream_calls == 3
    assert provider.complete_calls == 3


async def test_cold_start_insufficient_history_fails_closed(tmp_db: DbPool) -> None:
    """Fewer than sample_size eligible outcomes -> passed=False, no crash, no replay."""
    await _seed_outcomes(tmp_db, n=2)  # sample_size defaults to 5
    _live_registry, preg, provider = _build_registry_provider(
        stream_texts=["unused"], critic_scores=[0.9],
    )
    validator = ShadowValidator(tmp_db, preg)

    result = await validator.validate(_OWL_NAME, _manifest(), OwlDNA())

    assert result.passed is False
    assert result.consecutive_non_regressions == 0
    assert result.n_replayed == 2
    assert result.failures == ()
    # No replay was attempted at all.
    assert provider.stream_calls == 0
    assert provider.complete_calls == 0


async def test_isolation_live_registry_untouched(tmp_db: DbPool) -> None:
    """The LIVE OwlRegistry is never mutated by validate() — the concrete
    regression test for 'no side effects', not just asserting the return value."""
    await _seed_outcomes(tmp_db, n=5)
    live_registry, preg, _provider = _build_registry_provider(
        stream_texts=["ok"] * 5, critic_scores=[0.9] * 5,
    )
    original_dna = live_registry.get(_OWL_NAME).dna
    proposed_dna = original_dna.mutate("verbosity", 0.4)
    assert proposed_dna != original_dna

    validator = ShadowValidator(tmp_db, preg)
    await validator.validate(_OWL_NAME, live_registry.get(_OWL_NAME), proposed_dna)

    assert live_registry.get(_OWL_NAME).dna == original_dna
