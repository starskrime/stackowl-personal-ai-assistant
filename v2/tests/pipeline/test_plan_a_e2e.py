"""End-to-end multi-turn context regression — RC-B / RC-C.

Approach: FULL AsyncioBackend run (not manual step sequence).

The AsyncioBackend executes all PIPELINE_STEPS sequentially then calls
deliver.run. deliver is self-healing (returns state as-is when no
stream_registry is wired), so we can drive the full pipeline with only:
  - a SqliteMemoryBridge (in-memory temp DB via tmp_db fixture)
  - a ProviderRegistry holding a FakeProvider that records what it receives
  - an OwlRegistry with the default secretary manifest

Turn 1: "I am learning AWS"  → runs, consolidate persists the staged turn.
Turn 2: "what am I learning?" → classify reads back turn 1, assemble adds the
        secretary persona; execute receives BOTH in system_text + history.

Assertions (same session_id across turns):
  RC-B: turn-2 system_text contains the secretary persona text.
  RC-C: turn-2 history contains turn-1 user text "I am learning AWS".
  Persist: at least one staged conversation row exists after turn 1.
"""

from __future__ import annotations

import pytest

from stackowl.db.pool import DbPool
from stackowl.memory.sqlite_bridge import SqliteMemoryBridge
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.providers.base import CompletionResult, Message, ModelProvider
from stackowl.providers.registry import ProviderRegistry

pytestmark = pytest.mark.asyncio

# ---- Fake provider ----------------------------------------------------------


class _RecordingProvider(ModelProvider):
    """Provider that records the most recent system_text and history it receives.

    Returns a canned assistant reply so consolidate writes a meaningful turn.
    """

    def __init__(self) -> None:
        self._name = "fake"
        self.last_system_text: str | None = None
        self.last_history: list[Message] = []
        self._call_count = 0

    @property
    def name(self) -> str:
        return self._name

    @property
    def protocol(self) -> str:  # type: ignore[override]
        return "openai"

    async def complete(
        self, messages: list[Message], model: str, **kwargs: object
    ) -> CompletionResult:
        self._call_count += 1
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
        """Async generator: record system/history, yield one chunk."""
        self._call_count += 1
        # Messages arrive as [system?, *history, user]
        remaining = list(messages)
        if remaining and remaining[0].role == "system":
            self.last_system_text = remaining[0].content
            remaining = remaining[1:]
        # Everything except the last (user) turn is prior history.
        if len(remaining) > 1:
            self.last_history = list(remaining[:-1])
        else:
            self.last_history = []
        yield "canned reply "

    async def complete_with_tools(
        self,
        user_text: str,
        system_text: str | None,
        tool_schemas: list,
        tool_dispatcher,
        max_iterations: int = 8,
        history: list[Message] | None = None,
    ) -> tuple[str, list]:
        """Tool-loop path: record system_text and history."""
        self._call_count += 1
        self.last_system_text = system_text
        self.last_history = list(history or [])
        return "canned reply", []


# ---- Helpers ----------------------------------------------------------------


def _make_state(*, session_id: str, input_text: str) -> PipelineState:
    return PipelineState(
        trace_id=f"trace-{input_text[:8]}",
        session_id=session_id,
        input_text=input_text,
        channel="cli",
        owl_name="secretary",
        pipeline_step="start",
        interactive=True,
    )


def _build_services(
    bridge: SqliteMemoryBridge,
    provider: _RecordingProvider,
    owl_registry: OwlRegistry,
) -> StepServices:
    preg = ProviderRegistry()
    # Register under both "secretary" (per-owl lookup) AND the "powerful" tier
    # (tier-fallback path in execute.py), so both routing branches resolve.
    preg.register_mock("secretary", provider, tier="powerful")
    preg.register_mock("powerful", provider, tier="powerful")
    return StepServices(
        memory_bridge=bridge,
        provider_registry=preg,
        owl_registry=owl_registry,
        # tool_registry intentionally left None → streaming path (no tool loop)
    )


# ---- Tests ------------------------------------------------------------------


async def test_turn1_persists_staged_conversation_row(tmp_db: DbPool) -> None:
    """After turn 1 consolidate must write a staged conversation row (RC-C prereq)."""
    bridge = SqliteMemoryBridge(db=tmp_db)
    provider = _RecordingProvider()
    registry = OwlRegistry.with_default_secretary()
    services = _build_services(bridge, provider, registry)
    backend = AsyncioBackend(services=services)

    state = _make_state(session_id="sess-e2e", input_text="I am learning AWS")
    await backend.run(state)

    staged = await bridge.list_staged()
    convo = [s for s in staged if s.source_type == "conversation"]
    assert len(convo) >= 1, f"expected ≥1 conversation row, got {len(convo)}"
    assert any("I am learning AWS" in s.content for s in convo), (
        f"expected turn-1 text in staged content, got: {[s.content for s in convo]}"
    )


async def test_turn2_history_contains_turn1_user_text(tmp_db: DbPool) -> None:
    """RC-C: on turn 2 the provider's history must include turn-1 user text."""
    bridge = SqliteMemoryBridge(db=tmp_db)
    provider = _RecordingProvider()
    registry = OwlRegistry.with_default_secretary()
    services = _build_services(bridge, provider, registry)
    backend = AsyncioBackend(services=services)

    # Turn 1
    t1 = _make_state(session_id="sess-rc-c", input_text="I am learning AWS")
    await backend.run(t1)

    # Turn 2 — same session_id so classify fetches turn-1 from staged_facts
    t2 = _make_state(session_id="sess-rc-c", input_text="what am I learning?")
    await backend.run(t2)

    # At least one prior history entry must carry turn-1 user text.
    history_contents = [m.content for m in provider.last_history]
    assert any("I am learning AWS" in c for c in history_contents), (
        f"RC-C FAIL: expected 'I am learning AWS' in history on turn 2. "
        f"Got history contents: {history_contents}"
    )


async def test_turn2_system_text_contains_secretary_persona(tmp_db: DbPool) -> None:
    """RC-B: on turn 2 the provider's system_text must include the secretary persona."""
    bridge = SqliteMemoryBridge(db=tmp_db)
    provider = _RecordingProvider()
    registry = OwlRegistry.with_default_secretary()
    services = _build_services(bridge, provider, registry)
    backend = AsyncioBackend(services=services)

    # Turn 1
    t1 = _make_state(session_id="sess-rc-b", input_text="I am learning AWS")
    await backend.run(t1)

    # Turn 2
    t2 = _make_state(session_id="sess-rc-b", input_text="what am I learning?")
    await backend.run(t2)

    assert provider.last_system_text is not None, (
        "RC-B FAIL: system_text was None on turn 2 — assemble step did not run "
        "or provider was not called"
    )
    # The secretary manifest's system_prompt starts with "You are a helpful personal assistant."
    secretary_manifest = registry.get("secretary")
    persona_fragment = secretary_manifest.system_prompt.split("\n")[0]
    assert persona_fragment in provider.last_system_text, (
        f"RC-B FAIL: expected persona fragment {persona_fragment!r} in system_text. "
        f"Got: {provider.last_system_text!r}"
    )
