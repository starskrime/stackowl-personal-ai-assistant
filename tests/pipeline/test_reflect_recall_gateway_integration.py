"""Story 1.1 Task 3 (NFR-4) — reflect -> recall chain through the REAL gateway
turn pipeline, mocking ONLY the AI provider.

Mirrors ``tests/pipeline/test_plan_a_gateway_integration.py``'s pattern (real
``GatewayScanner``, real ``ProviderRegistry`` resolving a fake
``_RecordingProvider``, real ``AsyncioBackend``) rather than building a second
harness. Where that test proves persona+history reach the model, this proves
the SAME real ``classify`` step actually surfaces a published reflection
through ``_gather_lessons`` when driven end-to-end via ``backend.run()`` —
i.e. through ``triage`` -> ``dispatch`` -> ``classify`` -> ``assemble`` ->
``execute`` — not called directly in isolation the way Task 1's
``tests/memory/test_reflect_recall_chain_e2e.py`` does.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from tests._reflect_recall_chain_helpers import (
    NoOpCritic,
    ScriptedReflectionProvider,
    build_lessons_index,
    reflection_job,
    seed_outcome,
)

from stackowl.db.pool import DbPool
from stackowl.embeddings.registry import EmbeddingRegistry
from stackowl.gateway.scanner import GatewayScanner, IngressMessage
from stackowl.memory.reflection_writer_handler import ReflectionWriterHandler
from stackowl.memory.sqlite_bridge import SqliteMemoryBridge
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.providers.base import CompletionResult, Message, ModelProvider
from stackowl.providers.registry import ProviderRegistry
from stackowl.tools.registry import ToolRegistry

pytestmark = pytest.mark.asyncio

_SUMMARY = "web_fetch retries paid off for AWS billing questions"
_STRATEGY = "retry web_fetch once before giving up"


class _RecordingProvider(ModelProvider):
    """Records the ``system_text`` reaching the tool-loop path.

    Also answers the triage step's router fast-tier ``.complete()`` call — a
    canned reply that doesn't parse as a routing decision, so
    ``SecretaryRouter`` fail-safes to ``intent_class="standard"`` (see
    ``owls/router.py::_parse_intent_class``) while ``triage.run`` still
    unconditionally stamps ``intent_classified=True`` once ``route()``
    returns without raising. That combination is exactly AC #2's literal
    ``intent_class="standard"``/``intent_classified=True`` precondition,
    produced by the REAL router rather than hand-stamped on the state.

    Structurally identical to ``test_plan_a_gateway_integration.py``'s fake
    of the same name — reusing that pattern rather than building a second one
    (NFR-4 / Dev Notes).
    """

    def __init__(self) -> None:
        self._name = "fake"
        self.last_system_text: str | None = None
        self.tool_loop_calls = 0

    @property
    def name(self) -> str:
        return self._name

    @property
    def protocol(self) -> str:  # type: ignore[override]
        return "openai"

    async def complete(
        self, messages: list[Message], model: str, **kwargs: object
    ) -> CompletionResult:
        return CompletionResult(
            content="canned reply", input_tokens=10, output_tokens=3,
            model="fake-model", provider_name=self._name, duration_ms=1.0,
        )

    async def stream(  # type: ignore[override]
        self, messages: list[Message], model: str, **kwargs: object
    ) -> AsyncIterator[str]:
        yield "canned reply "

    async def complete_with_tools(
        self,
        user_text: str,
        system_text: str | None,
        tool_schemas: list,
        tool_dispatcher,
        max_iterations: int = 8,
        history: list[Message] | None = None,
        persistence_check=None,
        **_kwargs: object,
    ) -> tuple[str, list]:
        """Tool-loop path: record system_text, return zero tool calls."""
        self.tool_loop_calls += 1
        self.last_system_text = system_text
        return "canned reply", []


async def test_reflect_recall_chain_surfaces_in_live_turn_memory_context(
    tmp_db: DbPool, tmp_path: Path,
) -> None:
    """AC #2, driven through the live turn pipeline (NFR-4).

    A reflection is written + published (same helpers as Task 1), then a
    LATER turn is driven through the REAL ``GatewayScanner`` ->
    ``AsyncioBackend`` (``triage`` -> ``dispatch`` -> ``classify`` ->
    ``assemble`` -> ``execute``), with ONLY the AI provider mocked. The
    reflection's content must reach the tool-loop call's ``system_text``
    (``classify``'s ``memory_context`` folded in by ``assemble``).
    """
    lessons_index = build_lessons_index(tmp_path)
    bridge = SqliteMemoryBridge(db=tmp_db)
    owl_registry = OwlRegistry.with_default_secretary()
    tool_registry = ToolRegistry.with_defaults()
    assert tool_registry.all(), "tool_registry must be non-empty to force tool-loop branch"
    provider = _RecordingProvider()

    preg = ProviderRegistry()
    preg.register_mock("secretary", provider, tier="powerful")
    preg.register_mock("powerful", provider, tier="powerful")
    preg.register_mock("fast", provider, tier="fast")  # triage's router call

    # --- Seed + reflect the SAME way Task 1 does, into the SAME lessons_index -
    write_registry = ProviderRegistry()
    write_registry.register_mock(
        "fast", ScriptedReflectionProvider(_SUMMARY, _STRATEGY), tier="fast",
    )
    handler = ReflectionWriterHandler(
        db=tmp_db, provider_registry=write_registry,
        embedding_registry=EmbeddingRegistry(),
        critic=NoOpCritic(), lessons_index=lessons_index,
    )
    await seed_outcome(
        tmp_db, trace_id="trace-good-gw", owl_name="secretary",
        input_text="how do I check my AWS billing?",
        success=True, quality_score=0.85,
    )
    write_result = await handler.execute(reflection_job("reflection_writer-gw"))
    assert write_result.metadata["written"] == 1, (
        f"setup failed — reflection was not written: {write_result.metadata!r}"
    )

    services = StepServices(
        memory_bridge=bridge, provider_registry=preg, owl_registry=owl_registry,
        tool_registry=tool_registry, lessons_index=lessons_index, db_pool=tmp_db,
    )
    backend = AsyncioBackend(services=services)
    scanner = GatewayScanner(owl_registry=owl_registry)

    msg = IngressMessage(
        text="what do you know about AWS billing?",
        session_id="sess-reflect-recall-gw", channel="cli", trace_id="trace-gw-recall",
    )
    decision = scanner.scan(msg)
    assert decision.route == "owl", f"expected owl route, got {decision.route!r}"

    input_text = decision.stripped_text if decision.stripped_text is not None else msg.text
    state = PipelineState(
        trace_id=msg.trace_id, session_id=msg.session_id, input_text=input_text,
        channel=msg.channel, owl_name=decision.target, pipeline_step="start",
        interactive=True,
    )
    final_state = await backend.run(state)

    # Branch + precondition guards — if these fail, the break is upstream of
    # recall (routing/tool-loop wiring), not the reflect-recall chain itself.
    assert provider.tool_loop_calls >= 1, "TOOL-LOOP branch was not exercised"
    assert final_state.intent_class == "standard", (
        f"expected the router's fail-safe default, got {final_state.intent_class!r}"
    )
    assert final_state.intent_classified is True

    assert provider.last_system_text is not None, (
        "system_text was None — assemble step did not run or provider was "
        "not reached on the tool-loop path"
    )
    assert _SUMMARY in provider.last_system_text, (
        f"AC #2 FAILED (gateway-integration) — reflection never reached the "
        f"live turn's system_text. system_text={provider.last_system_text!r}"
    )
