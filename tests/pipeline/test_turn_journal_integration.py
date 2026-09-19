"""The "smallest REAL slice" AC (3/4): one real turn -- real ``AsyncioBackend``,
real ``ToolRegistry``/``OwlRegistry``, a real ``tmp_db`` wired as
``StepServices.db_pool`` -- drives one ``model.called``, one ``tool.called``
and one ``delegation.hopped`` row into ``journal_events``, all under ONE
``trace_id``, only the AI provider mocked (Story 2.7).

Mirrors ``tests/gateway/test_owl_turn_stream_delivery_integration.py``'s own
"smallest real slice" shape (see spec-2-7's Design Notes for why this harness
satisfies the ``scripts/dev_ingress.py``-driven AC without starting the live
process).
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from stackowl.authz.bounds import BoundsSpec, ResourceCaps
from stackowl.config.test_mode import TestModeGuard
from stackowl.db.pool import DbPool
from stackowl.messaging.a2a import A2AQueue
from stackowl.owls.a2a_delegation import A2ADelegator
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.providers.base import CompletionResult, ModelProvider
from stackowl.providers.registry import ProviderRegistry
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

pytestmark = pytest.mark.asyncio

_SECRETARY = "int_secretary"
_SCOUT = "int_scout"
_TOOL = "int_probe_tool"

# Synthetic canary -- never a real credential (NFR33: must never appear
# unredacted in any stored journal attrs).
CANARY = "Bearer sk-canary1234567890abcdefghijklmno"


@pytest.fixture(autouse=True)
def _live_io():  # noqa: ANN202
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]
    yield
    TestModeGuard._active = prev  # type: ignore[attr-defined]


class _ProbeTool(Tool):
    """A harmless synthetic tool the secretary calls once, carrying a canary
    secret in its arguments (NFR33's canary vehicle)."""

    @property
    def name(self) -> str:
        return _TOOL

    @property
    def description(self) -> str:
        return "a synthetic probe tool"

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {"note": {"type": "string"}}}

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self.name, description=self.description, parameters=self.parameters,
            action_severity="write", command_types=("test.probe",),
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        return ToolResult(success=True, output="probe ok", verified=True)


class _OneShotDelegatingProvider(ModelProvider):
    """Issues ONE real tool call plus ONE real ``delegate_task`` call on its
    FIRST invocation (the secretary's own turn); every later invocation (the
    delegated specialist's own turn) returns plain text with no tool calls,
    so the turn terminates instead of recursing.
    """

    def __init__(self) -> None:
        self._first_call = True

    @property
    def name(self) -> str:
        return "int-probe-provider"

    @property
    def protocol(self):  # noqa: ANN201
        return "openai"

    async def complete(self, messages, model, **kwargs):  # noqa: ANN001,ANN003,ANN201
        return CompletionResult(
            content="x", input_tokens=1, output_tokens=1,
            model="m", provider_name=self.name, duration_ms=1.0,
        )

    def stream(self, messages, model, **kwargs):  # noqa: ANN001,ANN003,ANN201
        raise NotImplementedError

    async def complete_with_tools(self, **kwargs: Any) -> tuple[str, list[Any]]:
        # A real provider's complete_with_tools wraps EVERY remote round in
        # _resilient_round (providers/base.py), which is the model.called
        # chokepoint this story wires. This fake stands in for that one round.
        async def _round() -> str:
            return "ok"

        await self._resilient_round(_round)

        dispatcher = kwargs["tool_dispatcher"]
        if self._first_call:
            self._first_call = False
            await dispatcher(_TOOL, {"note": CANARY})
            await dispatcher("delegate_task", {"goal": "help", "to_owl": _SCOUT})
            return "secretary done", []
        return "scout done", []


async def test_one_real_turn_records_all_three_event_types_under_one_trace(
    tmp_db: DbPool,
) -> None:
    provider = _OneShotDelegatingProvider()
    preg = ProviderRegistry()
    preg.register_mock(_SECRETARY, provider, tier="powerful")
    preg.register_mock(_SCOUT, provider, tier="powerful")
    preg.register_mock("powerful", provider, tier="powerful")
    # Production wiring (startup/orchestrator.py): the registry fans db_pool
    # out to every registered provider -- a bare test ProviderRegistry never
    # does this on its own, so it must be done explicitly here too.
    preg.set_db_pool(tmp_db)

    owl_registry = OwlRegistry()
    owl_registry.register(OwlAgentManifest(
        name=_SECRETARY, role="t", system_prompt="t", model_tier="powerful",
        bounds=BoundsSpec(
            tools=frozenset({_TOOL, "delegate_task"}), caps=ResourceCaps(max_steps=50),
        ),
    ))
    owl_registry.register(OwlAgentManifest(
        name=_SCOUT, role="t", system_prompt="t", model_tier="powerful",
        bounds=BoundsSpec(tools=frozenset(), caps=ResourceCaps(max_steps=50)),
    ))

    tool_registry = ToolRegistry.with_defaults()
    tool_registry.register(_ProbeTool())

    a2a_queue = A2AQueue()
    services = StepServices(
        provider_registry=preg, owl_registry=owl_registry, tool_registry=tool_registry,
        consent_gate=ConsequentialActionGate(confirm_fn=lambda _n: True),
        db_pool=tmp_db, a2a_queue=a2a_queue,
    )
    services.a2a_delegator = A2ADelegator(
        a2a_queue=a2a_queue, services=services, timeout_seconds=5.0,
    )

    trace_id = "trace-turn-integration-1"
    state = PipelineState(
        trace_id=trace_id, session_key="session-turn-integration-1", input_text="go",
        channel="cli", owl_name=_SECRETARY, pipeline_step="start", interactive=False,
    )
    backend = AsyncioBackend(services=services)

    await backend.run(state)

    rows = await tmp_db.fetch_all(
        "SELECT cursor, type, attrs FROM journal_events WHERE trace_id = ? "
        "AND type IN ('model.called', 'tool.called', 'delegation.hopped') "
        "ORDER BY cursor",
        (trace_id,),
    )
    types_seen = [r["type"] for r in rows]
    assert "model.called" in types_seen, f"no model.called row: {types_seen}"
    assert "tool.called" in types_seen, f"no tool.called row: {types_seen}"
    assert "delegation.hopped" in types_seen, f"no delegation.hopped row: {types_seen}"
    # The real causal FIRST-SEEN order (cursor-sorted): the secretary's own
    # remote round completes, then its tool dispatch, then the delegation hop
    # to the scout (whose OWN sub-turn contributes a second model.called, and
    # the probe tool's self-asserted verified=True is independently demoted
    # to unverified, triggering B4a's real same-tool retry -- a second
    # tool.called). Both are genuine platform behavior, not incidental noise,
    # so this asserts each type's FIRST cursor position rather than a rigid
    # row count -- still a real proof of causal order (a tautological
    # cursor-sort check would pass even if the write order were wrong, since
    # the query already orders by cursor; deduping to first-seen order does
    # not have that problem).
    first_seen_order = list(dict.fromkeys(types_seen))
    assert first_seen_order == ["model.called", "tool.called", "delegation.hopped"]

    # NFR33 — the canary seeded into the tool call's arguments never survives
    # unredacted into ANY stored journal attrs.
    for row in rows:
        assert CANARY not in row["attrs"]
        assert "sk-canary1234567890abcdefghijklmno" not in row["attrs"]

    tool_rows = [r for r in rows if r["type"] == "tool.called"]
    tool_attrs = json.loads(tool_rows[0]["attrs"])
    assert tool_attrs["tool_name"] == _TOOL
