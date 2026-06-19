"""Tests for TurnProgressTracker (Task 1 unit tests + Task 3 dispatch-integration tests)."""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from stackowl.authz.bounds import BoundsSpec, ResourceCaps
from stackowl.config.test_mode import TestModeGuard
from stackowl.infra import recovery_context, tool_outcome_ledger
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.progress_tracker import (
    NO_PROGRESS_THRESHOLD,
    TurnProgressTracker,
    resolve_no_progress_threshold,
)
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps.execute import _run_with_tools
from stackowl.pipeline.streaming import StreamRegistry
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

# ---------------------------------------------------------------------------
# Task 1 unit tests (unchanged from before, + two review-minor additions)
# ---------------------------------------------------------------------------

def test_threshold_default_is_three() -> None:
    assert NO_PROGRESS_THRESHOLD == 3


def test_resolve_threshold_scales_with_window() -> None:
    assert resolve_no_progress_threshold(8192) == 2      # lean → contain faster
    assert resolve_no_progress_threshold(4096) == 2
    assert resolve_no_progress_threshold(16384) == 3     # normal → default
    assert resolve_no_progress_threshold(None) == 3      # unknown → safe default


def test_no_progress_trips_at_threshold_and_bounces() -> None:
    t = TurnProgressTracker(threshold=3)
    assert t.record_no_progress("shell") is False  # 1
    assert t.record_no_progress("shell") is False  # 2
    assert t.record_no_progress("shell") is True   # 3 → opens NOW
    assert t.is_open("shell") is True
    assert t.opened_tools == ("shell",)
    # Review minor 1: a 4th call returns False (already open, no second True)
    assert t.record_no_progress("shell") is False


def test_success_resets_streak() -> None:
    t = TurnProgressTracker(threshold=3)
    t.record_no_progress("shell")
    t.record_no_progress("shell")
    t.record_progress("shell")                      # reset
    assert t.record_no_progress("shell") is False   # streak now 1
    assert t.is_open("shell") is False


def test_made_progress_flag() -> None:
    t = TurnProgressTracker(threshold=3)
    assert t.made_progress is False
    t.record_no_progress("shell")
    assert t.made_progress is False
    t.record_progress("http")
    assert t.made_progress is True


def test_scoped_per_tool() -> None:
    t = TurnProgressTracker(threshold=3)
    for _ in range(3):
        t.record_no_progress("shell")
    assert t.is_open("shell") is True
    assert t.is_open("http") is False


def test_state_progress_defaults_are_byte_identical() -> None:
    s = PipelineState(trace_id="t", session_id="s", input_text="x", channel="cli",
                      owl_name="o", pipeline_step="execute")
    # Default True ⇒ a turn that never entered the tracker is NEVER floored by it.
    assert s.turn_made_progress is True
    assert s.no_progress_tools == ()
    s2 = s.evolve(turn_made_progress=False, no_progress_tools=("shell",))
    assert s2.turn_made_progress is False
    assert s2.no_progress_tools == ("shell",)


# Review minor 2: open the circuit fully, then record_progress — is_open stays True
def test_open_stays_open_after_progress() -> None:
    t = TurnProgressTracker(threshold=3)
    t.record_no_progress("shell")
    t.record_no_progress("shell")
    t.record_no_progress("shell")   # circuit now open
    assert t.is_open("shell") is True
    t.record_progress("shell")      # success resets streak, but open stays open
    assert t.is_open("shell") is True


# ---------------------------------------------------------------------------
# Task 3 dispatch-integration tests — reuse harness from test_circuit_breaker.py
# ---------------------------------------------------------------------------

_OWL = "progress_owl"


@pytest.fixture(autouse=True)
def _live_io():  # noqa: ANN202
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]
    yield
    TestModeGuard._active = prev  # type: ignore[attr-defined]


class _CountingTool(Tool):
    """A tool whose success/failure/side_effect_committed per call is scripted."""

    def __init__(
        self,
        name: str,
        results: list[bool],
        severity: str = "write",
        side_effect_committed: bool = True,
        sleep_s: float = 0.0,
    ) -> None:
        self._name = name
        self._results = results
        self._severity = severity
        self._committed = side_effect_committed
        self._sleep_s = sleep_s
        # calls incremented at ENTRY (before any await) so timeout-cancelled calls
        # are still counted.
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
        # Increment BEFORE any await so timeout-cancelled executions are still counted.
        idx = self.calls
        self.calls += 1
        if self._sleep_s > 0:
            await asyncio.sleep(self._sleep_s)
        ok = self._results[idx] if idx < len(self._results) else self._results[-1]
        if ok:
            return ToolResult(success=True, output="ok", duration_ms=1.0)
        return ToolResult(
            success=False, output="", error="boom", duration_ms=1.0,
            side_effect_committed=self._committed,
        )


class _SeqProvider:
    """Dispatch a fixed sequence of (name, args) through the real _dispatch."""

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
    tools: list[Tool],
    calls: list[tuple[str, dict[str, object]]],
) -> tuple[_SeqProvider, PipelineState]:
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
        out = await _run_with_tools(state, provider, registry)  # type: ignore[arg-type]
        return provider, out
    finally:
        recovery_context.reset(recovery_token)
        tool_outcome_ledger.reset(ledger_token)
        reset_services(token)


# ---------------------------------------------------------------------------
# G1: timeout failures advance no-progress streak and bounce the tool
# ---------------------------------------------------------------------------

async def test_timeout_failures_advance_streak_and_bounce() -> None:
    """G1 — TimeoutError path closes the no-progress streak.

    Tool sleeps 0.2s; _TOOL_DEADLINE_S is monkeypatched to 0.05s so the real
    ``except TimeoutError`` fires every call. After `threshold` timeouts the
    tool must be bounced (not executed again).
    """
    import stackowl.pipeline.steps.execute as _exe

    threshold = 3
    # Sleep longer than the monkeypatched deadline so REAL TimeoutError fires.
    tool = _CountingTool("slow_tool", results=[False], sleep_s=0.2)
    calls = [("slow_tool", {"x": str(i)}) for i in range(threshold + 1)]

    orig_deadline = _exe._TOOL_DEADLINE_S
    try:
        _exe._TOOL_DEADLINE_S = 0.05
        provider, out = await _run([tool], calls)
    finally:
        _exe._TOOL_DEADLINE_S = orig_deadline

    # Only `threshold` real executions; the (threshold+1)-th is a circuit bounce.
    assert tool.calls == threshold, f"expected {threshold} executions, got {tool.calls}"
    assert "no longer available" in provider.rendered[threshold]
    assert "slow_tool" in provider.rendered[threshold]


# ---------------------------------------------------------------------------
# G2: no-op refusal (side_effect_committed=False) advances streak and bounces
# ---------------------------------------------------------------------------

async def test_refusal_failures_advance_streak_and_bounce() -> None:
    """G2 — side_effect_committed=False failures advance the no-progress streak.

    The ledger must NOT count these as consequential failures (committed=False).
    """
    threshold = 3
    # Tool always fails with committed=False (validation refusal shape).
    tool = _CountingTool(
        "noop_tool", results=[False], severity="write", side_effect_committed=False,
    )
    calls = [("noop_tool", {"x": str(i)}) for i in range(threshold + 1)]

    registry = ToolRegistry()
    registry.register(tool)
    owl_registry = OwlRegistry()
    owl_registry.register(OwlAgentManifest(
        name=_OWL, role="t", system_prompt="t", model_tier="fast",
        bounds=BoundsSpec(tools=frozenset({"noop_tool"}), caps=ResourceCaps(max_steps=50)),
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
        # Bounced after threshold; G2: ledger has 0 consequential failures.
        assert tool.calls == threshold, f"expected {threshold} executions, got {tool.calls}"
        assert "no longer available" in provider.rendered[threshold]
        assert cons_f == 0, f"expected 0 consequential failures, got {cons_f}"
    finally:
        recovery_context.reset(recovery_token)
        tool_outcome_ledger.reset(ledger_token)
        reset_services(token)


# ---------------------------------------------------------------------------
# State stamp tests
# ---------------------------------------------------------------------------

async def test_state_stamped_no_progress() -> None:
    """After a run where a tool failed enough times to OPEN its circuit (>= threshold),
    turn_made_progress is False and the bounced tool appears in no_progress_tools
    (which lists OPENED/bounced tools)."""
    tool = _CountingTool("shell", results=[False, False, False])
    calls = [("shell", {"x": str(i)}) for i in range(3)]
    _provider, out = await _run([tool], calls)
    assert out.turn_made_progress is False
    assert "shell" in out.no_progress_tools


async def test_state_stamped_made_progress() -> None:
    """A run with at least one successful tool → turn_made_progress True, no_progress_tools empty."""
    tool = _CountingTool("shell", results=[True])
    calls = [("shell", {"x": "1"})]
    _provider, out = await _run([tool], calls)
    assert out.turn_made_progress is True
    assert out.no_progress_tools == ()
