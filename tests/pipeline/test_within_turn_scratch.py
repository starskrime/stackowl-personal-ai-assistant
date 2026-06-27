"""ADR-5 MOVE 3 — ephemeral within-turn failed-approaches scratch (F-26/43/72).

When ``settings.trustworthy_learning`` is ON, a tool call that FAILED earlier this
turn with the EXACT same inputs is not blindly re-executed when the model re-issues
it: the dispatch consults a turn-scoped, never-persisted set of failed approaches and
returns a steer-to-a-different-approach refusal instead of running the tool again.

This is FINER than the same-tool circuit breaker (which is by tool NAME and only trips
after ``SAME_TOOL_FAILURE_THRESHOLD`` failures): the scratch keys on (tool, args) and
fires on the FIRST repeat. Default OFF ⇒ byte-identical (the repeat re-executes).
Positive-only directive honored: nothing is persisted — the set lives only for the turn.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import stackowl.config.settings as settings_mod
from stackowl.authz.bounds import BoundsSpec, ResourceCaps
from stackowl.config.test_mode import TestModeGuard
from stackowl.infra import recovery_context, tool_outcome_ledger
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps.execute import (
    TOOL_FAILED_MARKER,
    _run_with_tools,
)
from stackowl.pipeline.streaming import StreamRegistry
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

_OWL = "scratch_owl"


@pytest.fixture(autouse=True)
def _live_io():  # noqa: ANN202
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]
    yield
    TestModeGuard._active = prev  # type: ignore[attr-defined]


@pytest.fixture
def _flag(monkeypatch):  # noqa: ANN202
    """Set ``Settings().trustworthy_learning`` to the requested value."""

    def _set(value: bool) -> None:
        monkeypatch.setattr(
            settings_mod, "Settings", lambda: SimpleNamespace(trustworthy_learning=value)
        )

    return _set


class _CountingTool(Tool):
    """A tool whose success/failure per call is scripted; counts its executions."""

    def __init__(self, name: str, results: list[bool], severity: str = "write") -> None:
        self._name = name
        self._results = results
        self._severity = severity
        self.calls = 0

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"test tool {self._name}"

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {"x": {"type": "string"}}}

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self._name, description=self.description,
            parameters=self.parameters, action_severity=self._severity,
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        idx = self.calls
        self.calls += 1
        ok = self._results[idx] if idx < len(self._results) else self._results[-1]
        if ok:
            return ToolResult(success=True, output="ok", duration_ms=1.0)
        return ToolResult(success=False, output="", error="boom", duration_ms=1.0)


class _SeqProvider:
    """Drive a fixed sequence of (name, args) through the real _dispatch."""

    protocol = "anthropic"

    def __init__(self, calls: list[tuple[str, dict[str, object]]]) -> None:
        self._calls = calls
        self.rendered: list[str] = []

    async def complete_with_tools(  # noqa: ANN001
        self, *, user_text, system_text, tool_schemas, tool_dispatcher,
        history=None, on_iteration_complete=None, **_kwargs,
    ):
        records: list[dict[str, Any]] = []
        for name, args in self._calls:
            out = await tool_dispatcher(name, args)
            self.rendered.append(out)
            records.append({"name": name, "args": args, "result": out})
        return ("done", records)

    async def complete(self, messages, model, **kwargs):  # noqa: ANN201
        from stackowl.providers.base import CompletionResult
        return CompletionResult(
            content="x", input_tokens=1, output_tokens=1,
            model="m", provider_name=_OWL, duration_ms=1.0,
        )

    async def stream(self, *a, **k):  # pragma: no cover
        if False:  # noqa: SIM210
            yield ""


class _Reg:
    def __init__(self, p): self._p = p  # noqa: E704
    def get(self, name): return self._p  # noqa: E704
    def get_by_tier(self, tier): return self._p  # noqa: E704
    def get_with_cascade(self, t): return self._p  # noqa: E704


async def _run(
    tools: list[Tool], calls: list[tuple[str, dict[str, object]]]
) -> tuple[_SeqProvider, int, int]:
    registry = ToolRegistry()
    for t in tools:
        registry.register(t)
    owl_registry = OwlRegistry()
    owl_registry.register(OwlAgentManifest(
        name=_OWL, role="t", system_prompt="t", model_tier="fast",
        bounds=BoundsSpec(tools=frozenset(t.name for t in tools), caps=ResourceCaps(max_steps=50)),
    ))
    provider = _SeqProvider(calls)
    state = PipelineState(
        trace_id="t", session_id="s", input_text="x", channel="telegram",
        owl_name=_OWL, pipeline_step="execute", interactive=False,
    )
    token = set_services(StepServices(
        provider_registry=_Reg(provider),  # type: ignore[arg-type]
        tool_registry=registry, owl_registry=owl_registry,
        consent_gate=ConsequentialActionGate(confirm_fn=lambda _n: True),
        stream_registry=StreamRegistry(), cost_tracker=None,
    ))
    ledger_token = tool_outcome_ledger.bind()
    recovery_token = recovery_context.bind()
    try:
        await _run_with_tools(state, provider, registry)  # type: ignore[arg-type]
        cons_f, cons_s = tool_outcome_ledger.consequential_tally()
        return provider, cons_f, cons_s
    finally:
        recovery_context.reset(recovery_token)
        tool_outcome_ledger.reset(ledger_token)
        reset_services(token)


async def test_repeated_failed_approach_bounced_when_flag_on(_flag) -> None:
    _flag(True)
    # Same tool, SAME args, twice. Below the by-name threshold (3) — so any bounce on
    # the 2nd call is the (tool, args) scratch, not the circuit breaker.
    tool = _CountingTool("shell", results=[False, False])
    calls = [("shell", {"x": "1"}), ("shell", {"x": "1"})]
    provider, _cf, _cs = await _run([tool], calls)
    # The exact approach already failed → the repeat is NOT executed.
    assert tool.calls == 1, f"repeat of a failed approach must not re-run; got {tool.calls}"
    # Steers the model to change approach; NOT a tool-failure marker (containment).
    repeat = provider.rendered[1]
    assert TOOL_FAILED_MARKER not in repeat
    lower = repeat.lower()
    assert "different" in lower or "another" in lower or "same" in lower


async def test_repeated_failed_approach_executes_when_flag_off(_flag) -> None:
    _flag(False)
    # Byte-identical default: the same failed approach re-executes (no scratch consult).
    tool = _CountingTool("shell", results=[False, False])
    calls = [("shell", {"x": "1"}), ("shell", {"x": "1"})]
    provider, _cf, _cs = await _run([tool], calls)
    assert tool.calls == 2, "with the flag OFF the repeat must re-run (byte-identical)"


async def test_different_args_not_bounced_when_flag_on(_flag) -> None:
    _flag(True)
    # The scratch is approach-specific (tool+args): a DIFFERENT arg is a new approach.
    tool = _CountingTool("shell", results=[False, False])
    calls = [("shell", {"x": "1"}), ("shell", {"x": "2"})]
    provider, _cf, _cs = await _run([tool], calls)
    assert tool.calls == 2, "a different approach (new args) must still execute"


async def test_successful_approach_not_recorded_as_failed(_flag) -> None:
    _flag(True)
    # First call succeeds → never a failed approach → a later identical call still runs.
    tool = _CountingTool("shell", results=[True, True])
    calls = [("shell", {"x": "1"}), ("shell", {"x": "1"})]
    provider, _cf, _cs = await _run([tool], calls)
    assert tool.calls == 2, "a previously SUCCESSFUL approach must not be bounced"


async def test_scratch_bounce_records_no_effectful_failure(_flag) -> None:
    _flag(True)
    # The bounce must NOT add to the consequential failure tally (P0 honesty):
    # exactly one genuine write failure recorded, the bounce records nothing.
    tool = _CountingTool("shell", results=[False, False])
    calls = [("shell", {"x": "1"}), ("shell", {"x": "1"})]
    _provider, cons_f, _cs = await _run([tool], calls)
    assert cons_f == 1, f"expected exactly 1 recorded failure (no bounce double-count), got {cons_f}"
