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
from stackowl.providers.registry import ProviderRegistry

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

    @property
    def name(self) -> str:
        return "synth"

    async def complete(self, messages: list[Message], model: str, **kwargs: object) -> CompletionResult:
        idx = min(self.calls, len(self._outputs) - 1)
        content = self._outputs[idx]
        self.calls += 1
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
