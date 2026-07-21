"""Tests for the conservative STEER-vs-NEW classifier + two-stage turn-veto.

Task 15 (concurrent-msg §6.2/§6.3). Two layers under test:

1. ``ClarifyIntentClassifier.is_steer`` — the one-token verdict generalised for
   STEER-vs-NEW. Mirrors ``is_answer`` (fast-tier provider, ``max_tokens=4``,
   ``_parse_*`` one-token verdict) BUT with the OPPOSITE fail-safe direction:
   any error / ambiguity / missing-provider / timeout / empty → ``False`` (NEW),
   the cheap-and-visible direction. STEER is returned ONLY on a high-confidence
   STEER verdict (the asymmetric-cost safety principle).

2. ``TurnRouter.route`` — parses explicit signals first; on ``NONE`` consults
   ``is_steer``; a proposed STEER is then offered to the running turn's coherence
   judge (stage-2 veto, the D3 two-stage pattern) which can VETO an incoherent
   steer → NEW. Fail-safe everywhere → NEW.

Mock the fast-tier provider verdicts (as the ClarifyIntentClassifier tests do).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Literal

import pytest

from stackowl.gateway.turn_router import ExplicitSignal, TurnRouter
from stackowl.interaction.intent_classifier import ClarifyIntentClassifier
from stackowl.providers.base import CompletionResult, Message, ModelProvider

_RUNNING_ASK = "Draft the Q3 launch email to the marketing list."
_CORRECTION = "no, make it shorter"
_NEW_ASK = "what's the weather tomorrow?"


class _FakeProvider(ModelProvider):
    """Fast-tier provider stand-in honouring the real ModelProvider.complete sig."""

    def __init__(
        self,
        canned_verdict: str = "STEER",
        *,
        raise_on_complete: Exception | None = None,
        hang_seconds: float | None = None,
    ) -> None:
        self._verdict = canned_verdict
        self._raise = raise_on_complete
        self._hang_seconds = hang_seconds
        self.calls: list[list[Message]] = []

    @property
    def name(self) -> str:
        return "fake-fast"

    @property
    def protocol(self) -> Literal["openai", "anthropic", "gemini"]:
        return "openai"

    async def complete(
        self, messages: list[Message], model: str, **kwargs: object,
    ) -> CompletionResult:
        self.calls.append(list(messages))
        if self._hang_seconds is not None:
            await asyncio.sleep(self._hang_seconds)
        if self._raise is not None:
            raise self._raise
        return CompletionResult(
            content=self._verdict,
            input_tokens=1,
            output_tokens=1,
            model="fake-model",
            provider_name=self.name,
            duration_ms=1.0,
        )

    async def stream(  # pragma: no cover — unused by the classifier
        self, messages: list[Message], model: str, **kwargs: object,
    ) -> AsyncIterator[str]:
        yield ""


class _FakeRegistry:
    """Minimal registry: get_by_tier returns a provided (provider,
    model) pair (or raises)."""

    def __init__(
        self,
        provider: ModelProvider | None = None,
        *,
        raise_on_get: Exception | None = None,
    ) -> None:
        self._provider = provider
        self._raise = raise_on_get
        self.tiers_requested: list[str] = []

    def get_by_tier(self, tier: str) -> tuple[ModelProvider, str]:
        self.tiers_requested.append(tier)
        if self._raise is not None:
            raise self._raise
        assert self._provider is not None  # test wiring guarantee
        return self._provider, "fake-fast-model"


def _make_classifier(
    provider: ModelProvider | None = None,
    *,
    raise_on_get: Exception | None = None,
    timeout_s: float = 3.0,
) -> tuple[ClarifyIntentClassifier, _FakeRegistry]:
    registry = _FakeRegistry(provider, raise_on_get=raise_on_get)
    classifier = ClarifyIntentClassifier(registry, timeout_s=timeout_s)  # type: ignore[arg-type]
    return classifier, registry


# ====================================================================== is_steer
# Stage-1 conservative verdict. STEER ONLY at high confidence; everything else NEW.


@pytest.mark.asyncio
async def test_high_conf_steer_verdict_is_true() -> None:
    classifier, registry = _make_classifier(_FakeProvider("STEER"))
    out = await classifier.is_steer(running_ask=_RUNNING_ASK, message=_CORRECTION)
    assert out is True
    assert registry.tiers_requested == ["fast"]  # fast tier, as is_answer


@pytest.mark.asyncio
async def test_explicit_new_verdict_is_false() -> None:
    classifier, _ = _make_classifier(_FakeProvider("NEW"))
    out = await classifier.is_steer(running_ask=_RUNNING_ASK, message=_NEW_ASK)
    assert out is False


@pytest.mark.asyncio
async def test_uncertain_verdict_defaults_to_new_not_steer() -> None:
    """The conservative bias: an uncertain verdict → NEW (never STEER on doubt)."""
    classifier, _ = _make_classifier(_FakeProvider("uncertain"))
    out = await classifier.is_steer(running_ask=_RUNNING_ASK, message=_CORRECTION)
    assert out is False


@pytest.mark.asyncio
async def test_garbage_verdict_fail_safe_new() -> None:
    classifier, _ = _make_classifier(_FakeProvider("maybe?"))
    out = await classifier.is_steer(running_ask=_RUNNING_ASK, message=_CORRECTION)
    assert out is False


@pytest.mark.asyncio
async def test_both_tokens_ambiguous_fail_safe_new() -> None:
    """BOTH tokens, no clear leader → fail-safe NEW (opposite of is_answer)."""
    classifier, _ = _make_classifier(_FakeProvider("steer or new?"))
    out = await classifier.is_steer(running_ask=_RUNNING_ASK, message=_CORRECTION)
    assert out is False


@pytest.mark.asyncio
async def test_verbose_new_with_steer_token_is_false() -> None:
    """Leading token wins: 'NEW — do not steer the running turn' → NEW."""
    classifier, _ = _make_classifier(
        _FakeProvider("NEW — this does not steer the running turn"),
    )
    out = await classifier.is_steer(running_ask=_RUNNING_ASK, message=_NEW_ASK)
    assert out is False


@pytest.mark.asyncio
async def test_provider_raising_fail_safe_new() -> None:
    classifier, _ = _make_classifier(
        _FakeProvider(raise_on_complete=RuntimeError("boom")),
    )
    out = await classifier.is_steer(running_ask=_RUNNING_ASK, message=_CORRECTION)
    assert out is False


@pytest.mark.asyncio
async def test_no_provider_fail_safe_new() -> None:
    classifier, registry = _make_classifier(raise_on_get=RuntimeError("none"))
    out = await classifier.is_steer(running_ask=_RUNNING_ASK, message=_CORRECTION)
    assert out is False
    assert registry.tiers_requested == ["fast"]


@pytest.mark.asyncio
async def test_empty_message_fail_safe_new_without_provider_call() -> None:
    provider = _FakeProvider("STEER")  # would say STEER if ever called
    classifier, registry = _make_classifier(provider)
    out = await classifier.is_steer(running_ask=_RUNNING_ASK, message="   ")
    assert out is False
    assert registry.tiers_requested == []
    assert provider.calls == []


@pytest.mark.asyncio
async def test_hung_provider_fail_safe_new_quickly() -> None:
    classifier, _ = _make_classifier(_FakeProvider(hang_seconds=10.0), timeout_s=0.05)
    out = await asyncio.wait_for(
        classifier.is_steer(running_ask=_RUNNING_ASK, message=_CORRECTION),
        timeout=2.0,
    )
    assert out is False


@pytest.mark.asyncio
async def test_is_steer_never_raises_across_inputs() -> None:
    cases = [
        _make_classifier(_FakeProvider("STEER")),
        _make_classifier(_FakeProvider("NEW")),
        _make_classifier(_FakeProvider("maybe")),
        _make_classifier(_FakeProvider(raise_on_complete=ValueError("x"))),
        _make_classifier(raise_on_get=RuntimeError("none")),
    ]
    for classifier, _ in cases:
        out = await classifier.is_steer(running_ask=_RUNNING_ASK, message=_CORRECTION)
        assert isinstance(out, bool)


# ============================================================ is_steer_incoherent
# Stage-2 COHERENCE judge — the running turn's OWN veto on a proposed steer.
# DISTINCT from is_steer: is_steer asks refinement-vs-new (propose); this asks
# would-folding-this-blend-incoherently (coherence). Fail-safe → True (VETO→NEW),
# the SAFE direction (a wrong veto only yields a separate coherent answer).


@pytest.mark.asyncio
async def test_coherent_refinement_is_not_vetoed() -> None:
    """A genuine refinement (REFINE verdict) → False (no veto, STEER proceeds)."""
    classifier, registry = _make_classifier(_FakeProvider("REFINE"))
    out = await classifier.is_steer_incoherent(
        running_ask=_RUNNING_ASK, message="also include the 2023 data",
    )
    assert out is False
    assert registry.tiers_requested == ["fast"]  # fast tier, mirrors is_steer


@pytest.mark.asyncio
async def test_contradiction_goal_flip_is_vetoed() -> None:
    """A contradiction/goal-flip (CONFLICT verdict) → True (veto → NEW)."""
    classifier, _ = _make_classifier(_FakeProvider("CONFLICT"))
    out = await classifier.is_steer_incoherent(
        running_ask="Build the importer against MySQL.",
        message="no, do Postgres not MySQL",
    )
    assert out is True


@pytest.mark.asyncio
async def test_uncertain_verdict_fail_safe_veto() -> None:
    """An uncertain verdict → True (VETO → NEW), the SAFE direction on doubt."""
    classifier, _ = _make_classifier(_FakeProvider("uncertain"))
    out = await classifier.is_steer_incoherent(
        running_ask=_RUNNING_ASK, message=_CORRECTION,
    )
    assert out is True


@pytest.mark.asyncio
async def test_both_tokens_ambiguous_fail_safe_veto() -> None:
    """BOTH tokens, no clear leader → fail-safe VETO (the safe direction)."""
    classifier, _ = _make_classifier(_FakeProvider("unsure: refine or conflict"))
    out = await classifier.is_steer_incoherent(
        running_ask=_RUNNING_ASK, message=_CORRECTION,
    )
    assert out is True


@pytest.mark.asyncio
async def test_verbose_conflict_with_refine_token_is_vetoed() -> None:
    """Leading token wins: 'CONFLICT — would not refine' → veto."""
    classifier, _ = _make_classifier(
        _FakeProvider("CONFLICT — folding this would not refine the task"),
    )
    out = await classifier.is_steer_incoherent(
        running_ask=_RUNNING_ASK, message=_NEW_ASK,
    )
    assert out is True


@pytest.mark.asyncio
async def test_incoherent_provider_raising_fail_safe_veto() -> None:
    classifier, _ = _make_classifier(
        _FakeProvider(raise_on_complete=RuntimeError("boom")),
    )
    out = await classifier.is_steer_incoherent(
        running_ask=_RUNNING_ASK, message=_CORRECTION,
    )
    assert out is True


@pytest.mark.asyncio
async def test_incoherent_no_provider_fail_safe_veto() -> None:
    classifier, registry = _make_classifier(raise_on_get=RuntimeError("none"))
    out = await classifier.is_steer_incoherent(
        running_ask=_RUNNING_ASK, message=_CORRECTION,
    )
    assert out is True
    assert registry.tiers_requested == ["fast"]


@pytest.mark.asyncio
async def test_incoherent_empty_message_fail_safe_veto_no_call() -> None:
    provider = _FakeProvider("REFINE")  # would say REFINE if ever called
    classifier, registry = _make_classifier(provider)
    out = await classifier.is_steer_incoherent(running_ask=_RUNNING_ASK, message="   ")
    assert out is True  # empty → fail-safe VETO
    assert registry.tiers_requested == []
    assert provider.calls == []


@pytest.mark.asyncio
async def test_incoherent_hung_provider_fail_safe_veto_quickly() -> None:
    classifier, _ = _make_classifier(_FakeProvider(hang_seconds=10.0), timeout_s=0.05)
    out = await asyncio.wait_for(
        classifier.is_steer_incoherent(running_ask=_RUNNING_ASK, message=_CORRECTION),
        timeout=2.0,
    )
    assert out is True


@pytest.mark.asyncio
async def test_is_steer_incoherent_never_raises_across_inputs() -> None:
    cases = [
        _make_classifier(_FakeProvider("REFINE")),
        _make_classifier(_FakeProvider("CONFLICT")),
        _make_classifier(_FakeProvider("maybe")),
        _make_classifier(_FakeProvider(raise_on_complete=ValueError("x"))),
        _make_classifier(raise_on_get=RuntimeError("none")),
    ]
    for classifier, _ in cases:
        out = await classifier.is_steer_incoherent(
            running_ask=_RUNNING_ASK, message=_CORRECTION,
        )
        assert isinstance(out, bool)


@pytest.mark.asyncio
async def test_real_coherence_judge_wired_as_veto_vetoes_contradiction() -> None:
    """End-to-end: the REAL is_steer_incoherent wired as TurnRouter's turn_veto.

    Stage-1 says STEER (high conf), stage-2 coherence judge says CONFLICT → the
    router falls back to NEW. The veto provider is consulted via the real method.
    """
    classifier, _ = _make_classifier(_FakeProvider("STEER"))
    veto_classifier, _ = _make_classifier(_FakeProvider("CONFLICT"))
    router = TurnRouter(classifier, turn_veto=veto_classifier.is_steer_incoherent)
    signal = await router.route(running_ask=_RUNNING_ASK, message="no, do Y instead")
    assert signal is ExplicitSignal.NEW


@pytest.mark.asyncio
async def test_real_coherence_judge_wired_as_veto_allows_refinement() -> None:
    """Stage-1 STEER + stage-2 REFINE → the real wired judge lets STEER survive."""
    classifier, _ = _make_classifier(_FakeProvider("STEER"))
    veto_classifier, _ = _make_classifier(_FakeProvider("REFINE"))
    router = TurnRouter(classifier, turn_veto=veto_classifier.is_steer_incoherent)
    signal = await router.route(
        running_ask=_RUNNING_ASK, message="also include the 2023 data",
    )
    assert signal is ExplicitSignal.STEER


# ======================================================================= route
# The two-stage router: explicit signal → is_steer → turn-veto.


@pytest.mark.asyncio
async def test_route_explicit_steer_short_circuits_classifier() -> None:
    """An explicit /steer never touches the classifier (zero LLM cost)."""
    provider = _FakeProvider("NEW")  # would say NEW if ever consulted
    classifier, registry = _make_classifier(provider)
    router = TurnRouter(classifier)
    signal = await router.route(running_ask=_RUNNING_ASK, message="/steer fix it")
    assert signal is ExplicitSignal.STEER
    assert registry.tiers_requested == []  # classifier untouched


@pytest.mark.asyncio
async def test_route_explicit_stop_short_circuits() -> None:
    provider = _FakeProvider("STEER")
    classifier, registry = _make_classifier(provider)
    router = TurnRouter(classifier)
    signal = await router.route(running_ask=_RUNNING_ASK, message="/stop")
    assert signal is ExplicitSignal.STOP
    assert registry.tiers_requested == []


@pytest.mark.asyncio
async def test_route_unsignaled_high_conf_steer_becomes_steer() -> None:
    """No explicit signal + high-conf STEER + no veto → STEER."""
    classifier, _ = _make_classifier(_FakeProvider("STEER"))
    router = TurnRouter(classifier)
    signal = await router.route(running_ask=_RUNNING_ASK, message=_CORRECTION)
    assert signal is ExplicitSignal.STEER


@pytest.mark.asyncio
async def test_uncertain_defaults_to_new_steer_only_high_conf() -> None:
    """The classifier returns 'uncertain' → route NEW (never STEER on doubt)."""
    classifier, _ = _make_classifier(_FakeProvider("uncertain"))
    router = TurnRouter(classifier)
    signal = await router.route(running_ask=_RUNNING_ASK, message=_CORRECTION)
    assert signal is ExplicitSignal.NEW


@pytest.mark.asyncio
async def test_classifier_error_routes_new() -> None:
    classifier, _ = _make_classifier(
        _FakeProvider(raise_on_complete=RuntimeError("boom")),
    )
    router = TurnRouter(classifier)
    signal = await router.route(running_ask=_RUNNING_ASK, message=_CORRECTION)
    assert signal is ExplicitSignal.NEW


@pytest.mark.asyncio
async def test_high_conf_steer_but_turn_vetoes_becomes_new() -> None:
    """Stage-1 STEER (high conf); stage-2 turn-veto says 'doesn't fit' → NEW."""
    classifier, _ = _make_classifier(_FakeProvider("STEER"))
    vetoed: list[str] = []

    async def _veto(*, running_ask: str, message: str) -> bool:
        # The running turn's own coherence judge rejects the steer.
        vetoed.append(message)
        return True  # True == VETO (incoherent with the in-flight goal)

    router = TurnRouter(classifier, turn_veto=_veto)
    signal = await router.route(running_ask=_RUNNING_ASK, message=_CORRECTION)
    assert signal is ExplicitSignal.NEW
    assert vetoed == [_CORRECTION]  # the veto judge WAS consulted


@pytest.mark.asyncio
async def test_high_conf_steer_no_veto_stays_steer() -> None:
    """Stage-2 judge does NOT veto a coherent steer → STEER survives."""
    classifier, _ = _make_classifier(_FakeProvider("STEER"))

    async def _no_veto(*, running_ask: str, message: str) -> bool:
        return False  # coherent — do not veto

    router = TurnRouter(classifier, turn_veto=_no_veto)
    signal = await router.route(running_ask=_RUNNING_ASK, message=_CORRECTION)
    assert signal is ExplicitSignal.STEER


@pytest.mark.asyncio
async def test_veto_judge_raising_fail_safe_new() -> None:
    """A crashing veto judge must NOT poison the turn — fail-safe NEW."""
    classifier, _ = _make_classifier(_FakeProvider("STEER"))

    async def _boom(*, running_ask: str, message: str) -> bool:
        raise RuntimeError("veto judge crashed")

    router = TurnRouter(classifier, turn_veto=_boom)
    signal = await router.route(running_ask=_RUNNING_ASK, message=_CORRECTION)
    assert signal is ExplicitSignal.NEW


@pytest.mark.asyncio
async def test_veto_not_consulted_when_classifier_says_new() -> None:
    """No proposed STEER → the veto stage is skipped entirely."""
    classifier, _ = _make_classifier(_FakeProvider("NEW"))
    consulted: list[str] = []

    async def _veto(*, running_ask: str, message: str) -> bool:
        consulted.append(message)
        return True

    router = TurnRouter(classifier, turn_veto=_veto)
    signal = await router.route(running_ask=_RUNNING_ASK, message=_NEW_ASK)
    assert signal is ExplicitSignal.NEW
    assert consulted == []  # veto only runs on a PROPOSED steer


@pytest.mark.asyncio
async def test_route_never_raises() -> None:
    classifier, _ = _make_classifier(_FakeProvider("STEER"))

    async def _boom(*, running_ask: str, message: str) -> bool:
        raise RuntimeError("x")

    router = TurnRouter(classifier, turn_veto=_boom)
    for msg in ["/steer", "/stop", _CORRECTION, "", "  "]:
        signal = await router.route(running_ask=_RUNNING_ASK, message=msg)
        assert isinstance(signal, ExplicitSignal)
