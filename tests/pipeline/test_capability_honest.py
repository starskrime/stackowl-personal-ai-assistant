"""Task 7: capability-honest degradation on lean-window models.

Tests:
  1. synthesize_floor lean=False (and no kwarg) is BYTE-IDENTICAL to the
     pre-Task-7 baseline — this is the load-bearing regression guard.
  2. synthesize_floor lean=True returns a different, non-empty message that
     conveys the capability/window limitation.
  3. Gateway journeys:
     - lean model_window (≤ LEAN_WINDOW_THRESHOLD=8192) → tool bounced after 2
       failures, honest floor present.
     - normal model_window (16384) → tool bounced after 3, honest floor present
       (byte-compatible with Phase 1 threshold behaviour).
  4. The floor on a lean-window journey carries the lean phrasing.
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.authz.bounds import BoundsSpec, ResourceCaps
from stackowl.config.test_mode import TestModeGuard
from stackowl.infra import recovery_context, tool_outcome_ledger
from stackowl.owls.base_prompt import LEAN_WINDOW_THRESHOLD
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.delivery_gate import surface_consequential_giveup_floor
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps.execute import _run_with_tools
from stackowl.pipeline.streaming import StreamRegistry
from stackowl.pipeline.supervisor import synthesize_floor
from stackowl.setup.localize import localize
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

_OWL = "cap_honest_owl"
_REFUSAL_MARK = "no longer available"

# ---------------------------------------------------------------------------
# synthesize_floor unit tests
# ---------------------------------------------------------------------------

# Baseline values captured at test-write time — any regression that changes the
# lean=False output will make these fail immediately.
_BASELINE_GOAL = "make me a chart"
_BASELINE_CAP = "execute_code"


def _baseline_floor() -> str:
    """Current (pre-lean) output for our representative input."""
    return synthesize_floor(
        _BASELINE_GOAL,
        error="timeout",
        attempts=[_BASELINE_CAP],
        partial=None,
        failed_capability=_BASELINE_CAP,
        lang="en",
    )


def test_floor_message_byte_identical_when_not_lean() -> None:
    """lean=False (explicit) AND omitting the kwarg both produce the same output
    as the baseline — byte-identical, no suffix appended."""
    baseline = _baseline_floor()

    # Explicit lean=False
    explicit = synthesize_floor(
        _BASELINE_GOAL,
        error="timeout",
        attempts=[_BASELINE_CAP],
        partial=None,
        failed_capability=_BASELINE_CAP,
        lang="en",
        lean=False,
    )
    assert explicit == baseline, (
        f"lean=False changed the output — NOT byte-identical.\n"
        f"baseline: {baseline!r}\n"
        f"explicit: {explicit!r}"
    )

    # Omitted kwarg (default=False)
    omitted = synthesize_floor(
        _BASELINE_GOAL,
        error="timeout",
        attempts=[_BASELINE_CAP],
        partial=None,
        failed_capability=_BASELINE_CAP,
        lang="en",
    )
    assert omitted == baseline, (
        f"omitting lean= changed the output — NOT byte-identical.\n"
        f"baseline: {baseline!r}\n"
        f"omitted: {omitted!r}"
    )

    # Cross-check: baseline must be non-empty
    assert baseline.strip(), "baseline floor must be non-empty"


def test_floor_message_acknowledges_limit_when_lean() -> None:
    """lean=True appends an honest capability-limitation clause via the localization
    layer; the result differs from lean=False and is non-empty."""
    not_lean = synthesize_floor(
        _BASELINE_GOAL,
        error="timeout",
        attempts=[_BASELINE_CAP],
        partial=None,
        failed_capability=_BASELINE_CAP,
        lang="en",
        lean=False,
    )
    lean = synthesize_floor(
        _BASELINE_GOAL,
        error="timeout",
        attempts=[_BASELINE_CAP],
        partial=None,
        failed_capability=_BASELINE_CAP,
        lang="en",
        lean=True,
    )

    assert lean.strip(), "lean floor must be non-empty"
    assert lean != not_lean, (
        f"lean=True output must differ from lean=False.\n"
        f"lean=False: {not_lean!r}\n"
        f"lean=True:  {lean!r}"
    )
    # The lean suffix must appear in the output (proves the localization path ran)
    expected_suffix = localize("self_heal_floor_lean_suffix", "en")
    assert expected_suffix in lean, (
        f"lean floor does not contain the expected suffix.\n"
        f"expected suffix: {expected_suffix!r}\n"
        f"lean output: {lean!r}"
    )
    # The failed capability name must still be present
    assert _BASELINE_CAP in lean, (
        f"lean floor must still name the failed capability.\n"
        f"lean output: {lean!r}"
    )


def test_floor_message_lean_suffix_localized() -> None:
    """lean=True uses the localized suffix for non-English languages (de/fr/es)."""
    for lang in ("de", "fr", "es"):
        lean = synthesize_floor(
            "Erstelle ein Diagramm",
            error=None,
            attempts=[_BASELINE_CAP],
            partial=None,
            failed_capability=_BASELINE_CAP,
            lang=lang,
            lean=True,
        )
        expected_suffix = localize("self_heal_floor_lean_suffix", lang)
        assert expected_suffix in lean, (
            f"lean floor for lang={lang!r} missing localized suffix.\n"
            f"expected: {expected_suffix!r}\n"
            f"got: {lean!r}"
        )


# ---------------------------------------------------------------------------
# Gateway journey helpers (mirrors test_progress_supervisor_journey.py)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _live_io():  # noqa: ANN202
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]
    yield
    TestModeGuard._active = prev  # type: ignore[attr-defined]


class _AlwaysFailTool(Tool):
    """Tool that always fails — non-consequential so the no-progress tracker fires."""

    def __init__(self, name: str) -> None:
        self._name = name
        self.calls = 0

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"always-fail tool {self._name}"

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {"x": {"type": "string"}}}

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self._name,
            description=self.description,
            parameters=self.parameters,
            # Use "read" severity so the no-progress tracker (not the consequential
            # ledger) fires — this exercises the G2 path.
            action_severity="read",
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        self.calls += 1
        return ToolResult(
            success=False,
            side_effect_committed=False,
            output="",
            error="always fails",
            duration_ms=1.0,
        )


class _SpiralProvider:
    """Keep dispatching tool_name until refusal arrives."""

    protocol = "anthropic"

    def __init__(self, tool_name: str, max_attempts: int = 10) -> None:
        self._tool = tool_name
        self._max = max_attempts

    async def complete_with_tools(  # noqa: ANN001
        self,
        *,
        user_text,
        system_text,
        tool_schemas,
        tool_dispatcher,
        history=None,
        on_iteration_complete=None,
        **_kwargs,
    ):
        records: list[dict[str, Any]] = []
        for i in range(self._max):
            out = await tool_dispatcher(self._tool, {"x": str(i)})
            records.append({"name": self._tool, "args": {"x": str(i)}, "result": out})
            if _REFUSAL_MARK in out:
                break
        return ("OVERCLAIM: all done perfectly!", records)

    async def complete(self, messages, model, **kwargs):  # noqa: ANN201
        from stackowl.providers.base import CompletionResult

        return CompletionResult(
            content="x",
            input_tokens=1,
            output_tokens=1,
            model="m",
            provider_name=_OWL,
            duration_ms=1.0,
        )

    async def stream(self, *a, **k):  # pragma: no cover
        if False:  # noqa: SIM210
            yield ""


class _Reg:
    def __init__(self, p): self._p = p  # noqa: E704

    def get(self, name): return self._p  # noqa: E704

    def get_by_tier(self, tier): return self._p  # noqa: E704

    def get_with_cascade(self, t): return self._p  # noqa: E704


async def _drive(tool: _AlwaysFailTool, model_window: int | None) -> PipelineState:
    """Drive a full _run_with_tools + surface_consequential_giveup_floor pass
    with ``state.model_window`` set to ``model_window``."""
    registry = ToolRegistry()
    registry.register(tool)
    owl_registry = OwlRegistry()
    owl_registry.register(
        OwlAgentManifest(
            name=_OWL,
            role="t",
            system_prompt="t",
            model_tier="fast",
            bounds=BoundsSpec(
                tools=frozenset({tool.name}),
                caps=ResourceCaps(max_steps=50),
            ),
        )
    )
    state = PipelineState(
        trace_id="t",
        session_id="s",
        input_text="make me a chart",
        channel="telegram",
        owl_name=_OWL,
        pipeline_step="execute",
        interactive=False,
        model_window=model_window,
    )
    provider = _SpiralProvider(tool.name, max_attempts=10)
    token = set_services(
        StepServices(
            provider_registry=_Reg(provider),  # type: ignore[arg-type]
            tool_registry=registry,
            owl_registry=owl_registry,
            consent_gate=ConsequentialActionGate(confirm_fn=lambda _n: True),
            stream_registry=StreamRegistry(),
            cost_tracker=None,
        )
    )
    ledger_token = tool_outcome_ledger.bind()
    recovery_token = recovery_context.bind()
    try:
        out = await _run_with_tools(state, provider, registry)  # type: ignore[arg-type]
        return await surface_consequential_giveup_floor(out)
    finally:
        recovery_context.reset(recovery_token)
        tool_outcome_ledger.reset(ledger_token)
        reset_services(token)


# ---------------------------------------------------------------------------
# Threshold-scaling journey tests
# ---------------------------------------------------------------------------


async def test_lean_window_contains_faster() -> None:
    """A lean model_window (≤ LEAN_WINDOW_THRESHOLD) causes the tool to be
    bounced after exactly 2 failures (not 3) — the scaled threshold is live."""
    lean_window = LEAN_WINDOW_THRESHOLD  # exactly at the boundary
    tool = _AlwaysFailTool("chart_tool")
    out = await _drive(tool, model_window=lean_window)

    assert tool.calls == 2, (
        f"lean window must bounce after 2 failures, got {tool.calls}"
    )
    delivered = "".join(c.content for c in out.responses)
    assert "OVERCLAIM" not in delivered, f"OVERCLAIM shipped on lean window: {delivered!r}"
    assert any(getattr(c, "is_floor", False) for c in out.responses), (
        f"expected an honest is_floor chunk on lean window; got {out.responses!r}"
    )
    assert delivered.strip(), "lean-window floor must be non-empty"


async def test_lean_window_floor_carries_honest_degradation_phrasing() -> None:
    """The floor produced on a lean-window journey contains the lean suffix."""
    lean_window = LEAN_WINDOW_THRESHOLD
    tool = _AlwaysFailTool("chart_tool_phrasing")
    out = await _drive(tool, model_window=lean_window)

    delivered = "".join(c.content for c in out.responses)
    expected_suffix = localize("self_heal_floor_lean_suffix", "en")
    assert expected_suffix in delivered, (
        f"lean-window floor must contain the capability-limit suffix.\n"
        f"expected suffix: {expected_suffix!r}\n"
        f"floor delivered: {delivered!r}"
    )


async def test_normal_window_keeps_default() -> None:
    """A normal model_window (16384 > LEAN_WINDOW_THRESHOLD) keeps the default
    threshold of 3 — byte-compatible with Phase 1 baseline."""
    normal_window = 16384
    tool = _AlwaysFailTool("chart_tool_normal")
    out = await _drive(tool, model_window=normal_window)

    assert tool.calls == 3, (
        f"normal window must bounce after 3 failures, got {tool.calls}"
    )
    delivered = "".join(c.content for c in out.responses)
    assert "OVERCLAIM" not in delivered, f"OVERCLAIM shipped on normal window: {delivered!r}"
    assert any(getattr(c, "is_floor", False) for c in out.responses), (
        f"expected an honest is_floor chunk on normal window; got {out.responses!r}"
    )
    assert delivered.strip(), "normal-window floor must be non-empty"


async def test_none_window_keeps_default() -> None:
    """model_window=None (unknown) → default threshold 3, no lean phrasing."""
    tool = _AlwaysFailTool("chart_tool_none")
    out = await _drive(tool, model_window=None)

    assert tool.calls == 3, (
        f"unknown window must bounce after 3 failures, got {tool.calls}"
    )
    delivered = "".join(c.content for c in out.responses)
    # No lean suffix when window is unknown
    lean_suffix = localize("self_heal_floor_lean_suffix", "en")
    assert lean_suffix not in delivered, (
        f"lean suffix must NOT appear when model_window=None.\n"
        f"delivered: {delivered!r}"
    )
