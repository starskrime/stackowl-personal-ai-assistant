"""GATEWAY JOURNEY — Overclaim Delivery Gate (Task 6 — Turn Progress Supervisor).

FAILING-FIRST proof:
  The consequential give-up floor (surface_consequential_giveup_floor) does NOT
  catch every structural overclaim.  Specifically:

    Scenario: a turn where some tool succeeded (turn_made_progress=True) but ANOTHER
    tool bounced with no progress (no_progress_tools is non-empty), and nothing was
    delivered to the user (delivered_successes=()).

    - is_no_progress_giveup → False  (turn_made_progress=True; the predicate requires
      turn_made_progress=False to fire)
    - is_consequential_giveup_now → False  (no consequential failures recorded)
    - surface_consequential_giveup_floor → does NOT replace the confident draft
    - _is_overclaim → True  (no_progress_tools non-empty + delivered_successes=() +
      non-floor draft)
    - surface_overclaim_gate → REPLACES the confident draft with the honest floor

  The test drives the REAL _run_with_tools pipeline with a scripted provider to reach
  this mixed-progress state, then applies each gate in isolation to prove the gap.
  The AI provider is the ONLY mock; the real _run_with_tools / dispatch / ledger path
  runs.
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.authz.bounds import BoundsSpec, ResourceCaps
from stackowl.config.test_mode import TestModeGuard
from stackowl.infra import recovery_context, tool_outcome_ledger
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.delivery_gate import (
    is_consequential_giveup_now,
    is_no_progress_giveup,
    surface_consequential_giveup_floor,
    surface_overclaim_gate,
)
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps.execute import _run_with_tools
from stackowl.pipeline.streaming import ResponseChunk, StreamRegistry
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

_OWL = "oc_gate_owl"
_OVERCLAIM_TEXT = "I've successfully completed the task! Everything worked perfectly."
_REFUSAL_MARK = "no longer available"


# ---------------------------------------------------------------------------
# Fixture: keep TestModeGuard off so real pipeline path runs
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _live_io():  # noqa: ANN202
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]
    yield
    TestModeGuard._active = prev  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Tool stubs
# ---------------------------------------------------------------------------


class _SuccessTool(Tool):
    """Always succeeds — contributes to made_progress=True."""

    def __init__(self, name: str) -> None:
        self._name = name
        self.calls = 0

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"success tool {self._name}"

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {"x": {"type": "string"}}}

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self._name, description=self.description,
            parameters=self.parameters, action_severity="read",
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        self.calls += 1
        return ToolResult(success=True, output="data found", duration_ms=1.0)


class _BounceToolCommittedFalse(Tool):
    """Always fails with side_effect_committed=False (pre-flight refusal).

    Excluded from the consequential tally (is_effectful_failure → False).
    The no-progress tracker records the bounce and eventually stamps no_progress_tools.
    """

    def __init__(self, name: str) -> None:
        self._name = name
        self.calls = 0

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"bounce tool {self._name}"

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {"x": {"type": "string"}}}

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self._name, description=self.description,
            parameters=self.parameters, action_severity="write",
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        self.calls += 1
        return ToolResult(
            success=False,
            side_effect_committed=False,
            output="",
            error="pre-flight check failed",
            duration_ms=1.0,
        )


# ---------------------------------------------------------------------------
# Provider stub: call the success tool once, then spiral the bounce tool
# ---------------------------------------------------------------------------


class _MixedProgressProvider:
    """Calls success_tool once (establishes made_progress=True), then spirals bounce_tool
    until the circuit-breaker refusal, then emits a confident overclaim.

    Result on state:
    - turn_made_progress = True  (success_tool call recorded progress)
    - no_progress_tools = ("bounce_tool",)  (bounced at threshold)
    - delivered_successes = ()  (no consequential delivery recorded)
    - is_consequential_giveup_now = False  (no effectful failure)
    """

    protocol = "anthropic"

    def __init__(
        self,
        success_tool: str,
        bounce_tool: str,
        max_bounce_attempts: int = 10,
    ) -> None:
        self._success = success_tool
        self._bounce = bounce_tool
        self._max = max_bounce_attempts

    async def complete_with_tools(  # noqa: ANN001
        self, *, user_text, system_text, tool_schemas, tool_dispatcher,
        history=None, on_iteration_complete=None, **_kwargs,
    ):
        records: list[dict[str, Any]] = []
        # 1. Call the success tool once to set made_progress=True.
        out = await tool_dispatcher(self._success, {"x": "read"})
        records.append({"name": self._success, "args": {"x": "read"}, "result": out})
        # 2. Spiral the bounce tool until circuit-breaker.
        for i in range(self._max):
            out = await tool_dispatcher(self._bounce, {"x": str(i)})
            records.append({"name": self._bounce, "args": {"x": str(i)}, "result": out})
            if _REFUSAL_MARK in out:
                break
        # 3. Emit the confident overclaim — the weak model ignores the tool failures.
        return (_OVERCLAIM_TEXT, records)

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
    def __init__(self, p: Any) -> None:
        self._p = p

    def get(self, name: str) -> Any:
        return self._p

    def get_by_tier(self, tier: str) -> Any:
        return self._p

    def get_with_cascade(self, t: str) -> Any:
        return self._p


# ---------------------------------------------------------------------------
# Drive helper
# ---------------------------------------------------------------------------


async def _drive_to_post_floor(
    tools: list[Tool], provider: Any,
) -> PipelineState:
    """Run the real pipeline through _run_with_tools → surface_consequential_giveup_floor.

    Returns state AFTER the floor but BEFORE surface_overclaim_gate, so the
    failing-first proof can assert the overclaim still rides.
    """
    registry = ToolRegistry()
    for t in tools:
        registry.register(t)
    owl_registry = OwlRegistry()
    owl_registry.register(OwlAgentManifest(
        name=_OWL, role="t", system_prompt="t", model_tier="fast",
        bounds=BoundsSpec(
            tools=frozenset(t.name for t in tools),
            caps=ResourceCaps(max_steps=50),
        ),
    ))
    state = PipelineState(
        trace_id="t-oc-journey", session_id="s", input_text="send my report",
        channel="telegram", owl_name=_OWL, pipeline_step="execute", interactive=False,
    )
    svc_token = set_services(StepServices(
        provider_registry=_Reg(provider),  # type: ignore[arg-type]
        tool_registry=registry, owl_registry=owl_registry,
        consent_gate=ConsequentialActionGate(confirm_fn=lambda _n: True),
        stream_registry=StreamRegistry(), cost_tracker=None,
    ))
    ledger_token = tool_outcome_ledger.bind()
    recovery_token = recovery_context.bind()
    try:
        after_execute = await _run_with_tools(state, provider, registry)  # type: ignore[arg-type]
        # Apply ONLY the consequential floor — NOT the overclaim gate yet.
        return await surface_consequential_giveup_floor(after_execute)
    finally:
        recovery_context.reset(recovery_token)
        tool_outcome_ledger.reset(ledger_token)
        reset_services(svc_token)


# ---------------------------------------------------------------------------
# JOURNEY TEST
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_overclaim_gate_catches_what_floor_misses() -> None:
    """FAILING-FIRST proof: the consequential floor misses a mixed-progress overclaim;
    surface_overclaim_gate catches it.

    Scenario (mixed progress):
    - A read tool succeeds once → turn_made_progress=True
    - A write tool bounces repeatedly (committed=False) → no_progress_tools=("send_rpt",)
    - Provider emits confident overclaim
    - delivered_successes=() (read tool is non-consequential, nothing was sent)
    - is_consequential_giveup_now → False (no effectful failure; committed=False excluded)
    - is_no_progress_giveup → False (turn_made_progress=True blocks it)
    - surface_consequential_giveup_floor → DOES NOT fire (FAILING-FIRST gap)
    - surface_overclaim_gate → fires on no_progress_tools, stamps overclaim_blocked=True
    """
    success_tool = _SuccessTool("read_data")
    bounce_tool = _BounceToolCommittedFalse("send_rpt")
    provider = _MixedProgressProvider("read_data", "send_rpt", max_bounce_attempts=10)

    after_floor = await _drive_to_post_floor([success_tool, bounce_tool], provider)

    # -----------------------------------------------------------------------
    # FAILING-FIRST: verify the preconditions — floor did NOT fire.
    # -----------------------------------------------------------------------
    # The read tool succeeded → made_progress=True → is_no_progress_giveup=False.
    assert after_floor.turn_made_progress is True, (
        "Expected turn_made_progress=True (read_data succeeded). "
        f"Got: {after_floor.turn_made_progress}"
    )
    # The bounce tool is stuck → no_progress_tools is non-empty.
    assert after_floor.no_progress_tools, (
        f"Expected no_progress_tools to be non-empty; got: {after_floor.no_progress_tools!r}"
    )
    # Consequential floor predicates must both be False.
    assert not is_consequential_giveup_now(after_floor), (
        "is_consequential_giveup_now must be False (committed=False excluded from tally)"
    )
    assert not is_no_progress_giveup(after_floor), (
        "is_no_progress_giveup must be False (turn_made_progress=True blocks the predicate)"
    )
    # The overclaim text must still be present — floor did NOT replace it.
    draft_text = "".join(c.content for c in after_floor.responses)
    floor_fired = any(getattr(c, "is_floor", False) for c in after_floor.responses)
    assert not floor_fired, (
        f"surface_consequential_giveup_floor unexpectedly replaced the draft: {draft_text!r}. "
        "The failing-first scenario requires the floor NOT to fire here."
    )
    assert _OVERCLAIM_TEXT in draft_text, (
        f"Expected the overclaim text to ride after the floor; got: {draft_text!r}"
    )

    # -----------------------------------------------------------------------
    # THE GATE: surface_overclaim_gate catches the gap.
    # -----------------------------------------------------------------------
    after_gate = await surface_overclaim_gate(after_floor)

    assert after_gate.overclaim_blocked is True, (
        "surface_overclaim_gate must stamp overclaim_blocked=True"
    )
    gated_text = "".join(c.content for c in after_gate.responses)
    assert _OVERCLAIM_TEXT not in gated_text, (
        f"Overclaim text must be gone after the gate; got: {gated_text!r}"
    )
    assert any(getattr(c, "is_floor", False) for c in after_gate.responses), (
        f"Expected an is_floor=True chunk after the gate; got: {after_gate.responses!r}"
    )
    assert gated_text.strip(), "Honest floor must not be empty"
    # Floor names the stuck tool.
    assert "send_rpt" in gated_text, (
        f"Honest floor should name the stuck tool 'send_rpt'; got: {gated_text!r}"
    )
