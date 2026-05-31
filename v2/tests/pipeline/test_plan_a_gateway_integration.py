"""Gateway → backend integration regression for persona (RC-B) + history (RC-C)
on the PRODUCTION tool-loop branch.

Why this exists (vs the existing ``tests/pipeline/test_plan_a_e2e.py``):
  The existing e2e proves persona + history reach the model, but it (a) builds
  ``PipelineState`` by hand and (b) leaves ``tool_registry=None`` — which drives
  the STREAMING branch of ``pipeline/steps/execute.py``. Production runs the
  ``secretary`` owl WITH a real ``tool_registry`` (``ToolRegistry.with_defaults()``),
  so ``execute.run`` takes the TOOL-LOOP branch and calls
  ``provider.complete_with_tools(...)`` — a path the e2e never exercised.

What this test does differently:
  1. Drives the request through the REAL gateway: a message is fed to
     ``GatewayScanner.scan(IngressMessage(...))`` and we assert it routes to owl
     ``secretary`` (default route). The ``PipelineState`` is then built exactly
     the way ``startup/orchestrator.py`` builds it from a ``RouteDecision``
     (``owl_name=decision.target``, ``interactive=True``, ``pipeline_step="start"``).
  2. Wires a REAL, NON-EMPTY ``ToolRegistry.with_defaults()`` so
     ``tool_registry.all()`` is truthy → ``execute.run`` enters
     ``_run_with_tools`` (the tool-loop branch), NOT the streaming branch.
  3. The provider is resolved through the real ``ProviderRegistry`` (not a
     hand-injected provider on the streaming path). The resolved provider is a
     ``_RecordingProvider`` fake whose ``complete_with_tools`` records the
     ``system_text`` + ``history`` it receives and returns a canned ``(text, [])``
     (zero tool calls → the consent gate / dispatcher never runs).

Seam (documented): the orchestrator's CLI/telegram loop owns the IngressQueue
pump, stream_registry wiring, and clarify pump. Those are transport plumbing,
not the persona+history wiring under test. We reproduce the orchestrator's
scanner.scan → state-construction → backend.run sequence faithfully and stop at
that seam. The persona+history path (classify → assemble → execute) runs in full
through the real ``AsyncioBackend``.

Turn 1 seeds a prior turn directly into the bridge (history precondition).
Turn 2 asserts (same session_id):
  RC-B: turn-2 ``system_text`` contains the secretary persona text (tool-loop).
  RC-C: turn-2 ``history`` contains "I am learning AWS" (tool-loop).
"""

from __future__ import annotations

import pytest

from stackowl.db.pool import DbPool
from stackowl.gateway.scanner import GatewayScanner, IngressMessage
from stackowl.memory.sqlite_bridge import SqliteMemoryBridge
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.providers.base import CompletionResult, Message, ModelProvider
from stackowl.providers.registry import ProviderRegistry
from stackowl.tools.registry import ToolRegistry

pytestmark = pytest.mark.asyncio


# ---- Fake provider (resolved THROUGH the provider_registry) ------------------


class _RecordingProvider(ModelProvider):
    """Records the most recent system_text + history reaching the tool-loop path.

    ``complete_with_tools`` returns zero tool calls, so the dispatcher (and the
    consent gate behind it) never runs — only the persona+history wiring under
    test is exercised.
    """

    def __init__(self) -> None:
        self._name = "fake"
        self.last_system_text: str | None = None
        self.last_history: list[Message] = []
        self.tool_loop_calls = 0
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
        self.complete_calls += 1
        return CompletionResult(
            content="canned reply",
            input_tokens=10,
            output_tokens=3,
            model="fake-model",
            provider_name=self._name,
            duration_ms=1.0,
        )

    async def stream(  # type: ignore[override]
        self, messages: list[Message], model: str, **kwargs: object
    ):
        # If this is ever hit the test has silently fallen back to the streaming
        # branch — record it so the assertion below can catch the regression.
        self.stream_calls += 1
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
    ) -> tuple[str, list]:
        """Tool-loop path: record system_text + history, return zero tool calls."""
        self.tool_loop_calls += 1
        self.last_system_text = system_text
        self.last_history = list(history or [])
        return "canned reply", []


# ---- Helpers ----------------------------------------------------------------


def _build_services(
    bridge: SqliteMemoryBridge,
    provider: _RecordingProvider,
    owl_registry: OwlRegistry,
    tool_registry: ToolRegistry,
) -> StepServices:
    preg = ProviderRegistry()
    # Resolve the SAME fake under the per-owl key ("secretary") AND the
    # "powerful" tier (execute.run's tier-fallback path), so whichever lookup
    # execute takes lands on the RecordingProvider — never a real provider.
    preg.register_mock("secretary", provider, tier="powerful")
    preg.register_mock("powerful", provider, tier="powerful")
    return StepServices(
        memory_bridge=bridge,
        provider_registry=preg,
        owl_registry=owl_registry,
        # REAL, non-empty tool_registry → execute.run takes the TOOL-LOOP branch.
        tool_registry=tool_registry,
    )


def _state_from_decision(
    decision, *, trace_id: str, session_id: str, channel: str, raw_text: str
) -> PipelineState:
    """Build PipelineState exactly as startup/orchestrator.py does for an owl route."""
    input_text = decision.stripped_text if decision.stripped_text is not None else raw_text
    return PipelineState(
        trace_id=trace_id,
        session_id=session_id,
        input_text=input_text,
        channel=channel,
        owl_name=decision.target,
        pipeline_step="start",
        interactive=True,
    )


# ---- Test -------------------------------------------------------------------


async def test_gateway_to_backend_persona_and_history_on_tool_loop(tmp_db: DbPool) -> None:
    bridge = SqliteMemoryBridge(db=tmp_db)
    provider = _RecordingProvider()
    owl_registry = OwlRegistry.with_default_secretary()
    tool_registry = ToolRegistry.with_defaults()
    # Precondition for the test's own intent: a real, non-empty tool registry is
    # what forces the tool-loop branch in execute.run.
    assert tool_registry.all(), "tool_registry must be non-empty to force tool-loop branch"

    services = _build_services(bridge, provider, owl_registry, tool_registry)
    backend = AsyncioBackend(services=services)
    scanner = GatewayScanner(owl_registry=owl_registry)

    session_id = "sess-gw-tool-loop"

    # --- Seed ONE prior turn so history is non-empty on turn 2 ----------------
    await bridge.store("User: I am learning AWS\n\nAssistant: ok", session_id)

    # --- Turn (the user message) driven THROUGH the gateway scanner -----------
    msg = IngressMessage(
        text="what am I learning?",
        session_id=session_id,
        channel="cli",
        trace_id="trace-gw-1",
    )
    decision = scanner.scan(msg)
    # Gateway routing assertion: default message → owl route to secretary.
    assert decision.route == "owl", f"expected owl route, got {decision.route!r}"
    assert decision.target == "secretary", f"expected secretary, got {decision.target!r}"

    state = _state_from_decision(
        decision,
        trace_id=msg.trace_id,
        session_id=session_id,
        channel=msg.channel,
        raw_text=msg.text,
    )
    await backend.run(state)

    # --- Branch guard: the TOOL-LOOP path ran, NOT the streaming path ---------
    assert provider.tool_loop_calls >= 1, (
        "TOOL-LOOP branch was not exercised — complete_with_tools was never called. "
        f"(stream_calls={provider.stream_calls}, complete_calls={provider.complete_calls})"
    )
    assert provider.stream_calls == 0, (
        "Regression: execute fell back to the STREAMING branch "
        f"(stream_calls={provider.stream_calls}). The real tool_registry should force "
        "the tool-loop branch."
    )

    # --- RC-B: persona reached system_text on the tool-loop path --------------
    assert provider.last_system_text is not None, (
        "RC-B FAIL: system_text was None — assemble step did not run or provider "
        "was not reached on the tool-loop path."
    )
    secretary_manifest = owl_registry.get("secretary")
    persona_fragment = secretary_manifest.system_prompt.split("\n")[0]
    assert persona_fragment in provider.last_system_text, (
        f"RC-B FAIL: expected secretary persona fragment {persona_fragment!r} in "
        f"system_text. Got: {provider.last_system_text!r}"
    )

    # --- RC-C: prior-turn history reached the tool-loop path ------------------
    history_contents = [m.content for m in provider.last_history]
    assert any("I am learning AWS" in c for c in history_contents), (
        "RC-C FAIL: expected 'I am learning AWS' in history on the tool-loop path. "
        f"Got history contents: {history_contents}"
    )
