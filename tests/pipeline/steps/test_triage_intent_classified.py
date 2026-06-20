"""Tests for the intent_classified flag on PipelineState.

Story #2 — Failure-history is never injected on an unclassified/non-work turn.

The flag is set to True ONLY by the triage step when the SecretaryRouter
positively classifies the turn. Direct-address and error paths leave it False.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Literal

import pytest

from stackowl.pipeline.state import PipelineState


# ---------------------------------------------------------------------------
# Minimal state factory
# ---------------------------------------------------------------------------


def _state(**kw: object) -> PipelineState:
    base: dict[str, object] = dict(
        trace_id="t", session_id="s", input_text="hi", owl_name="secretary",
        channel="cli", pipeline_step="start",
    )
    base.update(kw)
    return PipelineState(**base)  # type: ignore[arg-type]


# ===========================================================================
# Step 1 / Step 4: intent_classified defaults to False
# ===========================================================================


def test_intent_classified_defaults_false() -> None:
    # A freshly minted turn has NOT been positively classified yet.
    assert _state().intent_classified is False


# ===========================================================================
# Step 5 / Step 8: triage step stamps intent_classified=True on secretary path,
# leaves it False on the direct-address path.
#
# Harness: drives the triage pipeline step directly (no gateway/adapter layer).
# Router providers mirrored from test_clarify_verdict_journey.py.
# ===========================================================================


from stackowl.owls.manifest import OwlAgentManifest  # noqa: E402
from stackowl.owls.registry import OwlRegistry  # noqa: E402
from stackowl.pipeline.services import StepServices, reset_services, set_services  # noqa: E402
from stackowl.pipeline.steps import triage as triage_step  # noqa: E402
from stackowl.providers.base import CompletionResult, Message, ModelProvider  # noqa: E402
from stackowl.providers.registry import ProviderRegistry  # noqa: E402


_JUDGE_SENTINEL = '{"delivered": true, "reason": "looks complete"}'


class _StandardRouterProvider(ModelProvider):
    """Fast-tier router that always classifies as 'standard'."""

    @property
    def name(self) -> str:
        return "standard-router-fake"

    @property
    def protocol(self) -> Literal["openai"]:
        return "openai"

    async def complete(
        self, messages: list[Message], model: str, **kwargs: object
    ) -> CompletionResult:
        joined = "\n".join(m.content for m in messages)
        content = (
            _JUDGE_SENTINEL if "AGENT DRAFT REPLY" in joined else "secretary\nstandard"
        )
        return CompletionResult(
            content=content,
            input_tokens=1,
            output_tokens=1,
            model="standard-router-fake",
            provider_name="standard-router-fake",
            duration_ms=0.0,
        )

    async def stream(  # type: ignore[override]
        self, messages: list[Message], model: str, **kwargs: object
    ) -> AsyncIterator[str]:
        yield "secretary\nstandard"


class _FakeAnswerProvider(ModelProvider):
    @property
    def name(self) -> str:
        return "fake-answer"

    @property
    def protocol(self) -> Any:  # type: ignore[override]
        return "openai"

    async def complete(
        self, messages: list[Message], model: str, **kwargs: object
    ) -> CompletionResult:
        return CompletionResult(
            content="ok", input_tokens=1, output_tokens=1,
            model="fake", provider_name="fake-answer", duration_ms=0.0,
        )

    async def stream(  # type: ignore[override]
        self, messages: list[Message], model: str, **kwargs: object
    ) -> AsyncIterator[str]:
        yield "ok"


def _build_services_standard(owl_registry: OwlRegistry) -> StepServices:
    """StepServices with a standard-classifying router."""
    preg = ProviderRegistry()
    answer = _FakeAnswerProvider()
    router = _StandardRouterProvider()
    preg.register_mock("secretary", answer, tier="powerful")
    preg.register_mock("powerful", answer, tier="powerful")
    preg.register_mock("router", router, tier="fast")
    preg.register_mock("local-judge", router, tier="local")
    return StepServices(provider_registry=preg, owl_registry=owl_registry)


@pytest.mark.asyncio
async def test_secretary_path_marks_classified() -> None:
    """A secretary-routed turn (standard class) must set intent_classified=True."""
    owl_registry = OwlRegistry.with_default_secretary()
    services = _build_services_standard(owl_registry)
    token = set_services(services)
    try:
        state = _state(owl_name="secretary", input_text="summarize my tasks")
        out = await triage_step.run(state)
        assert out.intent_classified is True
    finally:
        reset_services(token)


@pytest.mark.asyncio
async def test_direct_address_leaves_unclassified() -> None:
    """A direct-address turn (@scout hi) bypasses the router → intent_classified stays False."""
    owl_registry = OwlRegistry.with_default_secretary()
    # Register a "scout" owl so the direct-address path is the known-owl branch.
    owl_registry.register(
        OwlAgentManifest(name="scout", role="specialist", system_prompt="Scout owl.", model_tier="fast")
    )
    services = _build_services_standard(owl_registry)
    token = set_services(services)
    try:
        # GatewayScanner would set owl_name="scout" after stripping "@scout"; simulate that.
        state = _state(owl_name="scout", input_text="hi")
        out = await triage_step.run(state)
        assert out.intent_classified is False
        # The default must still be "standard" — this IS the bug's entry condition.
        assert out.intent_class == "standard"
    finally:
        reset_services(token)
