"""S3 F-57 — single-shot synthesis re-prompts once before accepting a degraded parse.

The synthesis ``provider.complete`` call used to be single-shot: a malformed or
empty completion was handed straight to :class:`SynthesisParser`, whose fallback
(S2 ``parse_ok=False``) then dressed the raw text as a verdict. A one-off bad
generation was therefore never recovered — it just degraded.

F-57 adds a BOUNDED retry: when the first completion is unparseable, the SAME
synthesis provider is re-prompted ONCE, stricter, before the result is accepted.
A persistently-unparseable synthesis stays ``parse_ok=False`` so the S2 gates
(orchestrator marks the session degraded; pellet generator skips staging) still
fire — this only recovers a transient bad generation; it never weakens those
gates. Covers both entry points: ``ParliamentSynthesizer.synthesize`` (session)
and ``synthesize_positions`` (MoA), which share the synthesis path.
"""

from __future__ import annotations

import pytest

from stackowl.config.test_mode import TestModeGuard
from stackowl.parliament.convergence import ConvergenceDetector
from stackowl.parliament.models import ParliamentRound, ParliamentSession
from stackowl.parliament.positions_synthesis import synthesize_positions
from stackowl.parliament.synthesis_parser import SynthesisParser
from stackowl.parliament.synthesizer import ParliamentSynthesizer
from stackowl.providers.base import CompletionResult, Message
from stackowl.providers.registry import ModelRoute, ProviderRegistry

_GOOD = "CONSENSUS: we agree on X\nRECOMMENDATION: ship it\n◆"
_BAD = "uh, the models just kind of rambled without any structure here"


@pytest.fixture(autouse=True)
def _live_io():  # noqa: ANN202
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]
    yield
    TestModeGuard._active = prev  # type: ignore[attr-defined]


class _ZeroConvergence(ConvergenceDetector):
    """Deterministic stand-in — never embeds (keeps the unit test offline)."""

    async def mean_similarity(self, responses: list[str]) -> float:  # noqa: D102
        return 0.0


class _ScriptedSynthProvider:
    """Synthesis LLM that returns a queued sequence of completions, counting calls."""

    protocol = "openai"

    def __init__(self, outputs: list[str]) -> None:
        self._outputs = outputs
        self.calls = 0
        self.seen_models: list[str] = []

    @property
    def name(self) -> str:
        return "synth"

    async def complete(self, messages: list[Message], model: str, **kwargs: object) -> CompletionResult:
        idx = min(self.calls, len(self._outputs) - 1)
        content = self._outputs[idx]
        self.calls += 1
        self.seen_models.append(model)
        return CompletionResult(
            content=content,
            input_tokens=8,
            output_tokens=16,
            model="synth-model",
            provider_name="synth",
            duration_ms=1.0,
        )

    async def stream(self, *a, **k):  # pragma: no cover — synthesis uses complete()
        if False:
            yield ""


def _registry(provider: _ScriptedSynthProvider) -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.register_mock("synth", provider, tier="powerful")  # type: ignore[arg-type]
    return registry


def _registry_with_model(provider: _ScriptedSynthProvider, model: str) -> ProviderRegistry:
    """Register ``provider`` on the "powerful" tier under an explicit model
    route, so resolution returns a non-default ``model`` string (Task 12 —
    proving the RESOLVED model reaches the provider's ``.complete(...)`` call,
    not just that a call happened)."""
    registry = ProviderRegistry()
    registry.register_mock(
        "synth",
        provider,  # type: ignore[arg-type]
        models=(ModelRoute(model=model, tiers=("powerful",)),),
    )
    return registry


def _session() -> ParliamentSession:
    return ParliamentSession(
        topic="should we ship?",
        owl_names=["scout", "sage"],
        session_id="sess-f57",
        rounds=[
            ParliamentRound(
                round_number=1,
                responses={"scout": "yes ship", "sage": "no, wait"},
                truncated={"scout": False, "sage": False},
            )
        ],
    )


# ---------------------------------------------------------------------------
# ParliamentSynthesizer.synthesize (session entry point)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_synthesize_reprompts_once_and_recovers() -> None:
    # First completion is unparseable; the retry returns a clean verdict.
    provider = _ScriptedSynthProvider([_BAD, _GOOD])
    synth = ParliamentSynthesizer(_registry(provider), _ZeroConvergence())

    result = await synth.synthesize(_session())

    assert provider.calls == 2, "expected exactly one re-prompt after a degraded parse"
    assert result.parse_ok is True, "the recovered verdict must be trusted"
    assert result.consensus == "we agree on X"
    assert result.recommendation == "ship it"


@pytest.mark.asyncio
async def test_synthesize_no_retry_when_first_parses() -> None:
    # A good first completion must NOT trigger a second (wasteful) call.
    provider = _ScriptedSynthProvider([_GOOD])
    synth = ParliamentSynthesizer(_registry(provider), _ZeroConvergence())

    result = await synth.synthesize(_session())

    assert provider.calls == 1
    assert result.parse_ok is True


@pytest.mark.asyncio
async def test_synthesize_persistent_failure_stays_degraded_bounded_to_one_retry() -> None:
    # Both attempts are unparseable -> result stays degraded (parse_ok False) and
    # the retry is bounded to exactly ONE extra call (no unbounded re-prompting).
    provider = _ScriptedSynthProvider([_BAD, _BAD])
    synth = ParliamentSynthesizer(_registry(provider), _ZeroConvergence())

    result = await synth.synthesize(_session())

    assert provider.calls == 2, "retry must be bounded to a single re-prompt"
    assert result.parse_ok is False, "persistently-unparseable synthesis stays degraded"


@pytest.mark.asyncio
async def test_synthesize_forwards_resolved_model_to_provider() -> None:
    # Task 12 — ParliamentSynthesizer.synthesize resolves via
    # resolve_capable_or_degrade_and_model, which must thread its resolved
    # model string all the way into complete_synthesis_with_retry's internal
    # provider.complete(...) call rather than the hardcoded model="".
    provider = _ScriptedSynthProvider([_GOOD])
    synth = ParliamentSynthesizer(
        _registry_with_model(provider, "claude-opus-4-synthesis"), _ZeroConvergence()
    )

    await synth.synthesize(_session())

    assert provider.seen_models == ["claude-opus-4-synthesis"]


@pytest.mark.asyncio
async def test_synthesize_forwards_resolved_model_to_provider_on_retry() -> None:
    # Task 12 review gap — the first-attempt regression above never exercises the
    # F-57 retry branch (line ~77's provider.complete(retry_messages, model=model)),
    # since it only ever queues a single GOOD output. Force the retry by queuing
    # BAD then GOOD, and assert the resolved model was threaded into BOTH calls.
    provider = _ScriptedSynthProvider([_BAD, _GOOD])
    synth = ParliamentSynthesizer(
        _registry_with_model(provider, "claude-opus-4-synthesis"), _ZeroConvergence()
    )

    result = await synth.synthesize(_session())

    assert provider.calls == 2, "expected exactly one re-prompt after a degraded parse"
    assert provider.seen_models == ["claude-opus-4-synthesis", "claude-opus-4-synthesis"], (
        "the resolved model must be threaded into both the first attempt AND the "
        "F-57 retry call, not just the first"
    )
    assert result.parse_ok is True


# ---------------------------------------------------------------------------
# synthesize_positions (MoA entry point — shares the synthesis path)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_synthesize_positions_reprompts_once_and_recovers() -> None:
    provider = _ScriptedSynthProvider([_BAD, _GOOD])
    result = await synthesize_positions(
        providers=_registry(provider),
        parser=SynthesisParser(),
        system_prompt="You are a synthesis engine. Use CONSENSUS:/RECOMMENDATION:/DISAGREEMENT:.",
        question="kuzu or lancedb?",
        positions=["go kuzu", "keep lancedb"],
    )

    assert provider.calls == 2
    assert result.parse_ok is True
    assert result.consensus == "we agree on X"


@pytest.mark.asyncio
async def test_synthesize_positions_persistent_failure_stays_degraded() -> None:
    provider = _ScriptedSynthProvider([_BAD, _BAD])
    result = await synthesize_positions(
        providers=_registry(provider),
        parser=SynthesisParser(),
        system_prompt="You are a synthesis engine. Use CONSENSUS:/RECOMMENDATION:/DISAGREEMENT:.",
        question="kuzu or lancedb?",
        positions=["go kuzu", "keep lancedb"],
    )

    assert provider.calls == 2
    assert result.parse_ok is False


@pytest.mark.asyncio
async def test_synthesize_positions_forwards_resolved_model_to_provider() -> None:
    # Task 12 — synthesize_positions (MoA entry point) resolves via
    # resolve_capable_or_degrade_and_model too; its complete_synthesis_with_retry
    # call must thread the resolved model, not hardcode model="".
    provider = _ScriptedSynthProvider([_GOOD])
    result = await synthesize_positions(
        providers=_registry_with_model(provider, "gpt-5.1-synthesis"),
        parser=SynthesisParser(),
        system_prompt="You are a synthesis engine. Use CONSENSUS:/RECOMMENDATION:/DISAGREEMENT:.",
        question="kuzu or lancedb?",
        positions=["go kuzu", "keep lancedb"],
    )

    assert provider.seen_models == ["gpt-5.1-synthesis"]
    assert result.parse_ok is True


@pytest.mark.asyncio
async def test_synthesize_positions_forwards_resolved_model_to_provider_on_retry() -> None:
    # Task 12 review gap — same retry-branch gap as
    # test_synthesize_forwards_resolved_model_to_provider_on_retry, but for the
    # MoA entry point which shares complete_synthesis_with_retry.
    provider = _ScriptedSynthProvider([_BAD, _GOOD])
    result = await synthesize_positions(
        providers=_registry_with_model(provider, "gpt-5.1-synthesis"),
        parser=SynthesisParser(),
        system_prompt="You are a synthesis engine. Use CONSENSUS:/RECOMMENDATION:/DISAGREEMENT:.",
        question="kuzu or lancedb?",
        positions=["go kuzu", "keep lancedb"],
    )

    assert provider.calls == 2
    assert provider.seen_models == ["gpt-5.1-synthesis", "gpt-5.1-synthesis"], (
        "the resolved model must be threaded into both the first attempt AND the "
        "F-57 retry call"
    )
    assert result.parse_ok is True
