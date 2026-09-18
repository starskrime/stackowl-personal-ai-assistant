"""``tool.called`` is recorded from the REAL ``_guarded_dispatch`` chokepoint
inside ``pipeline/steps/execute.py::_run_with_tools`` (Story 2.7) -- driven
through a real tool-loop turn, mirroring
``tests/pipeline/test_execute_hydrated_wiring.py``'s harness shape rather than
calling the recording helper directly.

Covers: a trustworthy success (no error_code), a claimed-but-unverified
effect (UNVERIFIED_EFFECT), a plain failure (TOOL_FAILED), a per-tool-deadline
timeout (TIMEOUT), and a pre-execution refusal (missing required parameter)
that must record NOTHING.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from stackowl.authz.bounds import BoundsSpec, ResourceCaps
from stackowl.config.test_mode import TestModeGuard
from stackowl.db.pool import DbPool
from stackowl.infra import recovery_context, tool_outcome_ledger
from stackowl.infra.trace import TraceContext
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps import execute as execute_module
from stackowl.pipeline.steps.execute import _run_with_tools
from stackowl.pipeline.streaming import StreamRegistry
from stackowl.providers.base import CompletionResult
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

pytestmark = pytest.mark.asyncio

_OWL = "probe_owl"
_TOOL = "probe_tool"


@pytest.fixture(autouse=True)
def _live_io():  # noqa: ANN202
    """Mirrors test_execute_hydrated_wiring.py -- tool.__call__ refuses to run
    under TestModeGuard, so this narrow harness must disable it."""
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]
    yield
    TestModeGuard._active = prev  # type: ignore[attr-defined]


class _ScenarioTool(Tool):
    """A tool whose ``execute()`` behavior is fixed by the test."""

    def __init__(self, behavior: str, *, required: bool = False) -> None:
        self._behavior = behavior
        self._required = required

    @property
    def name(self) -> str:
        return _TOOL

    @property
    def description(self) -> str:
        return "a scenario-driven probe tool"

    @property
    def parameters(self) -> dict[str, object]:
        props: dict[str, object] = {"x": {"type": "string"}}
        schema: dict[str, object] = {"type": "object", "properties": props}
        if self._required:
            schema["required"] = ["x"]
        return schema

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self.name, description=self.description, parameters=self.parameters,
            action_severity="write",
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        if self._behavior == "ok":
            return ToolResult(success=True, output="done", verified=True)
        if self._behavior == "unverified":
            return ToolResult(success=True, output="done", verified=False)
        if self._behavior == "failed":
            return ToolResult(success=False, output="", error="boom")
        if self._behavior == "hang":
            await asyncio.sleep(1000)
            return ToolResult(success=True, output="unreachable")
        raise AssertionError(f"unknown behavior {self._behavior!r}")


class _ToolCallingProvider:
    """Calls ``tool_dispatcher`` exactly once with the given name/args, then
    returns a final answer -- the minimal shape needed to drive ONE real
    ``_guarded_dispatch`` invocation through ``_run_with_tools``."""

    protocol = "anthropic"

    def __init__(self, tool_name: str, args: dict[str, Any]) -> None:
        self._tool_name = tool_name
        self._args = args

    async def complete_with_tools(self, **kwargs: Any) -> tuple[str, list[Any]]:
        dispatcher = kwargs["tool_dispatcher"]
        await dispatcher(self._tool_name, self._args)
        return "done", []

    async def complete(self, messages: Any, model: str, **kwargs: Any) -> Any:
        return CompletionResult(
            content="x", input_tokens=1, output_tokens=1,
            model="m", provider_name=_OWL, duration_ms=1.0,
        )

    async def stream(self, *a: Any, **k: Any):  # pragma: no cover
        if False:  # noqa: SIM210
            yield ""


class _Reg:
    def __init__(self, p: Any) -> None:
        self._p = p

    def get(self, name: str) -> Any:
        return self._p

    def get_by_tier(self, tier: str) -> Any:
        return self._p

    def get_with_cascade(self, t: Any) -> Any:
        return self._p


async def _run_scenario(
    tmp_db: DbPool, *, behavior: str, required: bool, trace_id: str,
    dispatch_args: dict[str, Any] | None = None,
) -> None:
    registry = ToolRegistry()
    registry.register(_ScenarioTool(behavior, required=required))
    owl_registry = OwlRegistry()
    owl_registry.register(OwlAgentManifest(
        name=_OWL, role="t", system_prompt="t", model_tier="fast",
        bounds=BoundsSpec(tools=frozenset({_TOOL}), caps=ResourceCaps(max_steps=50)),
    ))
    provider = _ToolCallingProvider(_TOOL, dispatch_args if dispatch_args is not None else {"x": "v"})
    state = PipelineState(
        trace_id=trace_id, session_key=f"session-{trace_id}", input_text="x",
        channel="telegram", owl_name=_OWL, pipeline_step="execute", interactive=False,
    )
    token = set_services(StepServices(
        provider_registry=_Reg(provider),  # type: ignore[arg-type]
        tool_registry=registry, owl_registry=owl_registry,
        consent_gate=ConsequentialActionGate(confirm_fn=lambda _n: True),
        stream_registry=StreamRegistry(), cost_tracker=None, db_pool=tmp_db,
    ))
    ledger_token = tool_outcome_ledger.bind()
    recovery_token = recovery_context.bind()
    # `_run_with_tools` is called directly here (bypassing
    # `pipeline.backends.shared.bind_turn_context`, which a real
    # `AsyncioBackend.run()` call would use) -- so TraceContext must be
    # established explicitly, the same way every other narrow test in this
    # story does, or `record_tool_call` sees no trace_id and silently skips.
    trace_token = TraceContext.start(trace_id=trace_id, owl_name=_OWL)
    try:
        await _run_with_tools(state, provider, registry)  # type: ignore[arg-type]
    finally:
        TraceContext.reset(trace_token)
        recovery_context.reset(recovery_token)
        tool_outcome_ledger.reset(ledger_token)
        reset_services(token)


async def _tool_called_rows(tmp_db: DbPool, trace_id: str) -> list:
    return await tmp_db.fetch_all(
        "SELECT * FROM journal_events WHERE trace_id = ? AND type = 'tool.called'",
        (trace_id,),
    )


class TestATrustworthySuccessRecordsOkWithNoErrorCode:
    async def test_success(self, tmp_db: DbPool) -> None:
        await _run_scenario(tmp_db, behavior="ok", required=False, trace_id="trace-tool-ok")
        rows = await _tool_called_rows(tmp_db, "trace-tool-ok")
        assert len(rows) == 1
        row = rows[0]
        assert row["outcome"] == "ok"
        assert row["target_id"] == _TOOL
        assert row["actor_kind"] == "owl"
        assert row["actor_id"] == _OWL
        attrs = json.loads(row["attrs"])
        assert attrs["error_code"] is None
        assert attrs["action_severity"] == "write"


class TestAnUnverifiedEffectRecordsItsOwnErrorCode:
    async def test_unverified(self, tmp_db: DbPool) -> None:
        """B4a: an unverified effect triggers ONE automatic same-tool retry
        (``_guarded_dispatch`` is shared by the initial dispatch and the
        retry so both record IDENTICALLY) -- a REAL second dispatch, so it
        earns its own ``tool.called`` row too. This scenario tool always
        returns ``verified=False``, so BOTH rows carry the same error_code."""
        await _run_scenario(
            tmp_db, behavior="unverified", required=False, trace_id="trace-tool-unverified",
        )
        rows = await _tool_called_rows(tmp_db, "trace-tool-unverified")
        assert len(rows) == 2
        for row in rows:
            assert row["outcome"] == "ok"  # r.success was True
            attrs = json.loads(row["attrs"])
            assert attrs["error_code"] == "unverified_effect"


class TestAPlainFailureRecordsToolFailed:
    async def test_failed(self, tmp_db: DbPool) -> None:
        await _run_scenario(tmp_db, behavior="failed", required=False, trace_id="trace-tool-failed")
        rows = await _tool_called_rows(tmp_db, "trace-tool-failed")
        assert len(rows) == 1
        assert rows[0]["outcome"] == "failed"
        attrs = json.loads(rows[0]["attrs"])
        assert attrs["error_code"] == "tool_failed"


class TestATimeoutRecordsTimeout:
    async def test_timeout(self, tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch) -> None:
        # Shrink the per-tool deadline so a genuinely hanging tool times out fast.
        monkeypatch.setattr(execute_module, "_TOOL_DEADLINE_S", 0.05)
        await _run_scenario(tmp_db, behavior="hang", required=False, trace_id="trace-tool-timeout")
        rows = await _tool_called_rows(tmp_db, "trace-tool-timeout")
        assert len(rows) == 1
        assert rows[0]["outcome"] == "failed"
        attrs = json.loads(rows[0]["attrs"])
        assert attrs["error_code"] == "timeout"


class TestAPreExecutionRefusalRecordsNothing:
    """The P0 honesty invariant: denied_this_run / deterministic_dead /
    circuit-open / missing-param refusals already 'record nothing' by
    convention -- only a REAL dispatch is journaled. Missing-required-param
    is the easiest of the four pre-execution branches to trigger directly."""

    async def test_missing_required_parameter(self, tmp_db: DbPool) -> None:
        await _run_scenario(
            tmp_db, behavior="ok", required=True, trace_id="trace-tool-refused",
            dispatch_args={},  # omits the required "x"
        )
        rows = await _tool_called_rows(tmp_db, "trace-tool-refused")
        assert rows == []
