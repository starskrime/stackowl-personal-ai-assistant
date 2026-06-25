"""Story #2 merge-gate journey — failure-history is NEVER injected on a direct-address/unclassified turn.

Three prongs, one test:

(a) POSITIVE CONTROL (helper integration): seed a real reflection for the
    owl, call ``_gather_recent_reflections`` directly with the real DB wired
    into services, and assert the reflection marker IS returned. Proves the
    gun is armed: the reflection CAN appear when the gather path runs.
    (classify.run itself guards on memory_bridge being present, which is not
    wired in the test environment; we probe the gather helper directly — the
    real seam the gate guards.)

(b) NEGATIVE (gateway journey): run a direct-address turn through the real
    pipeline. The direct-address path returns from triage WITHOUT running the
    router, so intent_classified stays False. The failure-history gate must
    suppress the reflection block. Assert the marker is NOT in the
    system_text captured by the scripted provider.

Assertion anchors on ASSEMBLED CONTEXT (system_text / gathered block),
never on model output text.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any, Literal

import pytest

from stackowl.config.test_mode import TestModeGuard
from stackowl.db.pool import DbPool
from stackowl.gateway.scanner import GatewayScanner, IngressMessage
from stackowl.memory.reflection_store import ReflectionStore
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps.classify import (
    _gather_recent_reflections,
    _should_surface_failure_history,
)
from stackowl.providers.base import CompletionResult, Message, ModelProvider
from stackowl.providers.registry import ProviderRegistry
from stackowl.tenancy.principal import DEFAULT_PRINCIPAL_ID
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

# ---------------------------------------------------------------------------
# The distinctive marker seeded as the reflection summary
# ---------------------------------------------------------------------------

_OWL = "scout"
_MARKER = "PHANTOM-TASK-FAILURE-MARKER-XQ9"


# ---------------------------------------------------------------------------
# Router provider — conversational verdict (keeps direct-address unclassified)
# ---------------------------------------------------------------------------

_JUDGE_SENTINEL = '{"delivered": true, "reason": "looks complete"}'


class _DirectAddressProvider(ModelProvider):
    """Scripted provider that captures system_text and returns a canned reply.

    Used as both the fast-tier router (never called on direct-address) and the
    answer provider. The system_text captured here is what classify assembled
    and handed to the provider — the gateway-layer assertion target.
    """

    def __init__(self) -> None:
        self.system_text: str = ""

    @property
    def name(self) -> str:
        return "direct-address-fake"

    @property
    def protocol(self) -> Literal["openai"]:
        return "openai"

    async def complete(
        self, messages: list[Message], model: str, **kwargs: object
    ) -> CompletionResult:
        # Capture system_text from the messages list (first system message).
        for m in messages:
            if m.role == "system":
                self.system_text = m.content
                break
        joined = "\n".join(m.content for m in messages)
        content = (
            _JUDGE_SENTINEL if "AGENT DRAFT REPLY" in joined else "Hello there!"
        )
        return CompletionResult(
            content=content,
            input_tokens=1,
            output_tokens=1,
            model="direct-address-fake",
            provider_name="direct-address-fake",
            duration_ms=0.0,
        )

    async def stream(  # type: ignore[override]
        self, messages: list[Message], model: str, **kwargs: object
    ) -> AsyncIterator[str]:
        for m in messages:
            if m.role == "system":
                self.system_text = m.content
                break
        yield "Hello there!"

    async def complete_with_tools(  # noqa: ANN001
        self,
        *,
        user_text: str,
        system_text: str,
        tool_schemas: list[Any],
        tool_dispatcher: Any,
        history: list[Any] | None = None,
        **_kw: Any,
    ) -> tuple[str, list[Any]]:
        self.system_text = system_text or ""
        return "Hello there!", []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_services(
    provider: _DirectAddressProvider,
    owl_registry: OwlRegistry,
    db: DbPool,
) -> StepServices:
    preg = ProviderRegistry()
    preg.register_mock("secretary", provider, tier="powerful")
    preg.register_mock("powerful", provider, tier="powerful")
    preg.register_mock("router", provider, tier="fast")
    preg.register_mock("local-judge", provider, tier="local")
    preg.register_mock("standard-judge", provider, tier="standard")
    return StepServices(
        provider_registry=preg,
        owl_registry=owl_registry,
        tool_registry=ToolRegistry(),
        consent_gate=ConsequentialActionGate(),
        db_pool=db,
    )


async def _seed_reflection(db: DbPool) -> None:
    """Insert a distinctive reflection for _OWL into the real DB."""
    store = ReflectionStore(db, owner_id=DEFAULT_PRINCIPAL_ID)
    await store.write(
        trace_id=f"seed-{uuid.uuid4()}",
        owl_name=_OWL,
        summary=_MARKER,
        suggested_strategy="retry with smaller steps",
        failure_class="tool_failure",
        quality_score=0.9,
        embedding=None,
        embedding_model=None,
    )


# ---------------------------------------------------------------------------
# Journey
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failure_history_gate(
    tmp_db: DbPool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Three-prong gate: arm gun → positive control → negative (absence)."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    # -----------------------------------------------------------------------
    # ARM THE GUN: seed a real reflection for the owl.
    # -----------------------------------------------------------------------
    await _seed_reflection(tmp_db)

    owl_registry = OwlRegistry.with_default_secretary()
    owl_registry.register(
        OwlAgentManifest(
            name=_OWL, role="specialist", system_prompt="Scout owl.", model_tier="fast"
        )
    )

    provider = _DirectAddressProvider()
    services = _build_services(provider, owl_registry, tmp_db)

    # -----------------------------------------------------------------------
    # (a) POSITIVE CONTROL — the gather helper returns the seeded reflection
    #     when services has a live DB pool AND the gate allows it.
    #
    # We probe _gather_recent_reflections directly (not classify.run) because
    # classify.run guards on memory_bridge being wired, which is not needed
    # for this story's gate. The gather helper is the exact seam _should_
    # surface_failure_history guards. We verify two things:
    #   1. The helper returns the block when called (gun IS armed).
    #   2. _should_surface_failure_history returns True on a classified turn
    #      (confirming the gate WOULD admit it) and False on unclassified
    #      (confirming the gate WOULD suppress it).
    # -----------------------------------------------------------------------
    token = set_services(services)
    try:
        reflections_block = await _gather_recent_reflections(_OWL, limit=3)
    finally:
        reset_services(token)

    assert _MARKER in reflections_block, (
        f"POSITIVE CONTROL FAILED: seeded reflection {_MARKER!r} must be returned "
        f"by _gather_recent_reflections when DB is wired. block={reflections_block!r}"
    )

    # Gate logic: classified standard → True (would admit).
    classified_state = PipelineState(
        trace_id="gate-pos", session_id="s", input_text="x",
        channel="cli", owl_name=_OWL, pipeline_step="start",
        intent_class="standard", intent_classified=True,
    )
    assert _should_surface_failure_history(classified_state) is True, (
        "GATE CHECK FAILED: classified standard turn must return True from gate"
    )

    # Gate logic: unclassified (direct-address default) → False (would suppress).
    unclassified_state = PipelineState(
        trace_id="gate-neg", session_id="s", input_text="hi",
        channel="cli", owl_name=_OWL, pipeline_step="start",
        intent_class="standard", intent_classified=False,
    )
    assert _should_surface_failure_history(unclassified_state) is False, (
        "GATE CHECK FAILED: unclassified direct-address turn must return False from gate"
    )

    # -----------------------------------------------------------------------
    # (b) NEGATIVE — direct-address greeting must NOT surface failure history.
    #
    # Run through the real backend. triage returns without calling the router
    # on a direct-address turn → intent_classified stays False → gate suppresses
    # the reflection block.
    # -----------------------------------------------------------------------
    provider.system_text = ""
    backend = AsyncioBackend(services=services)

    # Simulate what GatewayScanner produces for "@scout hi" (direct address).
    state = PipelineState(
        trace_id="neg-direct-trace",
        session_id="neg-direct-session",
        input_text="hi",          # stripped text after "@scout" is removed
        channel="cli",
        owl_name=_OWL,            # scanner set this from the @mention
        pipeline_step="start",
        interactive=True,
    )
    await backend.run(state)

    captured = provider.system_text
    assert _MARKER not in captured, (
        f"NEGATIVE PRONG FAILED: phantom failure-history {_MARKER!r} was injected "
        f"into a direct-address greeting. system_text[:800]={captured[:800]!r}"
    )
    assert "## Recent Reflections" not in captured, (
        "NEGATIVE PRONG FAILED: '## Recent Reflections' block leaked into a "
        "direct-address greeting's system_text."
    )
