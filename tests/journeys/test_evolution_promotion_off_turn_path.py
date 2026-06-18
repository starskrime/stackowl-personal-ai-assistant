"""Off-path guard (concurrent-msg §4.6 invariant).

LOCKS the invariant that DNA-evolution and memory-promotion are OFF the turn's
critical path. The recon confirmed:

  * Evolution = ``owls/evolution.py`` ``EvolutionCoordinator`` — a scheduler
    ``JobHandler`` (``handler_name="evolution_batch"``), driven by the scheduler,
    NOT by a turn.
  * Promotion = ``FactPromoter.promote_eligible`` — invoked ONLY from
    ``memory/dream_worker.py`` (the DreamWorker job), NOT by a turn.
  * ``consolidate._persist_turn`` only STAGES a fact (``bridge.store`` → a single
    INSERT, no embed, no lock). It triggers neither evolution nor promotion.

Because evolution/promotion are off-path, cross-session concurrency CANNOT create
concurrent *inline* evolution/promotion (the race Winston feared). This test drives
two concurrent cross-session turns through the REAL pipeline (mirroring
``tests/pipeline/test_plan_a_gateway_integration.py``: real ``ToolRegistry``,
real ``AsyncioBackend``, a recording provider resolved through the real
``ProviderRegistry``) with spies on ``EvolutionCoordinator.execute`` and
``FactPromoter.promote_eligible``, and asserts NEITHER is invoked during the turns.

If a future change ever moves evolution/promotion ON the turn path, this test
FAILS LOUDLY — surfacing the concurrency race before it can ship.

NOTE on spy targets: the plan sketch named ``EvolutionCoordinator.handle``/``run``,
but the LIVE method on the ``JobHandler`` is ``execute`` (verified in
``owls/evolution.py``). Per "trust live code", the spy targets the real method.
"""

from __future__ import annotations

import asyncio

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


class _RecordingProvider(ModelProvider):
    """Resolved THROUGH the provider_registry; returns zero tool calls so the
    turn runs end-to-end (classify → assemble → execute → consolidate → deliver)
    without the dispatcher/consent gate."""

    def __init__(self) -> None:
        self._name = "fake"
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
        **_kwargs,
    ) -> tuple[str, list]:
        self.tool_loop_calls += 1
        return "canned reply", []


def _build_services(
    bridge: SqliteMemoryBridge,
    provider: _RecordingProvider,
    owl_registry: OwlRegistry,
    tool_registry: ToolRegistry,
) -> StepServices:
    preg = ProviderRegistry()
    preg.register_mock("secretary", provider, tier="powerful")
    preg.register_mock("powerful", provider, tier="powerful")
    return StepServices(
        memory_bridge=bridge,
        provider_registry=preg,
        owl_registry=owl_registry,
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


async def test_concurrent_turns_do_not_inline_evolution_or_promotion(
    tmp_db: DbPool, monkeypatch
) -> None:
    """Two concurrent cross-session turns must NOT inline evolution/promotion.

    Spies record any call to ``EvolutionCoordinator.execute`` (the JobHandler
    entrypoint) and ``FactPromoter.promote_eligible`` (the DreamWorker entrypoint).
    Both must stay empty: a turn stages only.
    """
    evo_calls: list[str] = []
    promo_calls: list[str] = []

    async def _spy_evo(self, *a: object, **k: object) -> object:  # noqa: ANN001
        evo_calls.append("evo")
        return None

    async def _spy_promo(self, *a: object, **k: object) -> int:  # noqa: ANN001
        promo_calls.append("promo")
        return 0

    # Spy the REAL method names (live: JobHandler.execute, not the plan's
    # handle/run). raising=True so a rename of either entrypoint fails this test
    # loudly rather than silently no-op'ing the guard.
    monkeypatch.setattr(
        "stackowl.owls.evolution.EvolutionCoordinator.execute", _spy_evo, raising=True
    )
    monkeypatch.setattr(
        "stackowl.memory.fact_promoter.FactPromoter.promote_eligible",
        _spy_promo,
        raising=True,
    )

    # Count staging calls: a turn must STAGE exactly once (no embed, no promote).
    store_calls: list[str] = []
    real_store = SqliteMemoryBridge.store

    async def _counting_store(self, content, session_id, *, trust=None):  # noqa: ANN001
        store_calls.append(session_id)
        return await real_store(self, content, session_id, trust=trust)

    monkeypatch.setattr(SqliteMemoryBridge, "store", _counting_store, raising=True)

    bridge = SqliteMemoryBridge(db=tmp_db)
    provider = _RecordingProvider()
    owl_registry = OwlRegistry.with_default_secretary()
    tool_registry = ToolRegistry.with_defaults()
    assert tool_registry.all(), "tool_registry must be non-empty to force tool-loop branch"

    services = _build_services(bridge, provider, owl_registry, tool_registry)
    backend = AsyncioBackend(services=services)
    scanner = GatewayScanner(owl_registry=owl_registry)

    # --- Build two concurrent cross-session turns (same owl, distinct sessions) ---
    sessions = ("sess-off-path-1", "sess-off-path-2")
    states: list[PipelineState] = []
    for idx, session_id in enumerate(sessions):
        msg = IngressMessage(
            text="what am I learning?",
            session_id=session_id,
            channel="cli",
            trace_id=f"trace-off-path-{idx}",
        )
        decision = scanner.scan(msg)
        assert decision.route == "owl", f"expected owl route, got {decision.route!r}"
        assert decision.target == "secretary", f"expected secretary, got {decision.target!r}"
        states.append(
            _state_from_decision(
                decision,
                trace_id=msg.trace_id,
                session_id=session_id,
                channel=msg.channel,
                raw_text=msg.text,
            )
        )

    # --- Dispatch BOTH turns concurrently through the real backend ----------------
    await asyncio.gather(*(backend.run(s) for s in states))

    # --- Branch guard: the tool-loop path actually ran for both turns -------------
    assert provider.tool_loop_calls == 2, (
        "both turns must have run the tool-loop path "
        f"(complete_with_tools); got {provider.tool_loop_calls}"
    )

    # --- LOAD-BEARING: evolution + promotion never ran inline on the turn path ----
    assert evo_calls == [], "evolution must stay off the turn path (§4.6 invariant)"
    assert promo_calls == [], "promotion must stay off the turn path (§4.6 invariant)"

    # --- consolidate only STAGED: exactly one bridge.store per turn ---------------
    assert sorted(store_calls) == sorted(sessions), (
        "consolidate must STAGE exactly one fact per turn (no inline embed/promote); "
        f"got store calls for {store_calls}"
    )
