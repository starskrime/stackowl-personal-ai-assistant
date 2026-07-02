"""GATEWAY JOURNEY — a budget-cap cutoff with an INCIDENTAL-only success must floor.

Live incident reproduced: user asked "can you help me with pictures". A weak model
spiraled, the default 120s time backstop fired (BudgetBreach), and the turn shipped an
OVERCLAIM partial ("...these are real image files that will look gorgeous on your
phone!") — no image was ever delivered. The honest floor
(``surface_consequential_giveup_floor``) should have replaced the overclaim but did not,
because the model's trailing ``write_file`` SUCCEEDED (an incidental workspace effect the
user never received) and the give-up predicate counted any consequential/write success as
"the outcome was achieved".

GOAL-RELEVANT ARTIFACT ACCOUNTING (the fix): at a BUDGET-CAP cutoff a success disarms the
honest floor only if it is GOAL-RELEVANT — i.e. it crossed the boundary OUT to the user
(``action_severity == "consequential"``: send_file / send_message). An INCIDENTAL ``write``
(local workspace mutation) alongside a consequential FAILURE, when the turn was cut off by
the budget cap, must ship the honest floor, not the overclaim.

Driven at the ``_run_with_tools`` seam (the same direct integration boundary
test_budget_cap.py Journey 2 uses), then run through the backend's exact pre-delivery
step ``surface_consequential_giveup_floor`` — the AI provider is the ONLY mock. The budget
cap is squeezed tiny (max_steps=1) so BudgetBreach fires deterministically WITHOUT sleeping.
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.authz.bounds import BoundsSpec, ResourceCaps
from stackowl.config.test_mode import TestModeGuard
from stackowl.infra import recovery_context, tool_outcome_ledger
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.delivery_gate import surface_consequential_giveup_floor
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps.execute import _run_with_tools
from stackowl.pipeline.streaming import StreamRegistry
from stackowl.providers.react_callback import ReActIterationState
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

_OWL_NAME = "pictures_owl"
_OVERCLAIM = (
    "Now let me create the visual diagrams as proper SVG image files — these are "
    "real image files that will look gorgeous on your phone!"
)


@pytest.fixture(autouse=True)
def _live_io():  # noqa: ANN202
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]
    yield
    TestModeGuard._active = prev  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# REAL tools — a failing CONSEQUENTIAL send + a succeeding INCIDENTAL write.
# ---------------------------------------------------------------------------


class _FailingSendImageTool(Tool):
    """A consequential 'deliver an image to the user' tool that FAILS."""

    @property
    def name(self) -> str:
        return "send_image"

    @property
    def description(self) -> str:
        return "Send an image file out to the user."

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {"path": {"type": "string"}}}

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self.name, description=self.description, parameters=self.parameters,
            action_severity="consequential",
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        # Crossed the boundary and FAILED — side_effect_committed stays True (default).
        return ToolResult(success=False, output="", error="delivery backend down", duration_ms=1.0)


class _IncidentalWriteTool(Tool):
    """A write-severity local workspace mutation that SUCCEEDS (never delivered)."""

    @property
    def name(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return "Write content to a local workspace file."

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {"path": {"type": "string"}}}

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self.name, description=self.description, parameters=self.parameters,
            action_severity="write",
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        return ToolResult(success=True, output="Written: diagram.svg", duration_ms=1.0)


class _DelegateTaskTool(Tool):
    """A WRITE-severity boundary-crossing dispatch that SUCCEEDS — delegated work
    that genuinely crossed the boundary OUT. NOT a local file mutation, so it counts
    as a delivered/goal-relevant success even though its severity is 'write'."""

    @property
    def name(self) -> str:
        return "delegate_task"

    @property
    def description(self) -> str:
        return "Dispatch a sub-task to a specialist owl."

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {"task": {"type": "string"}}}

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self.name, description=self.description, parameters=self.parameters,
            action_severity="write",
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        return ToolResult(success=True, output="Dispatched to specialist.", duration_ms=1.0)


class _DeliveredSendTool(Tool):
    """A consequential delivery tool that SUCCEEDS — a goal-relevant artifact."""

    @property
    def name(self) -> str:
        return "send_image"

    @property
    def description(self) -> str:
        return "Send an image file out to the user."

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {"path": {"type": "string"}}}

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self.name, description=self.description, parameters=self.parameters,
            action_severity="consequential",
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        return ToolResult(success=True, output="Sent: diagram.svg", duration_ms=1.0)


# ---------------------------------------------------------------------------
# THE ONLY MOCK — a scripted provider that dispatches real tools then overclaims.
# ---------------------------------------------------------------------------


class _ScriptedProvider:
    """Dispatch a scripted sequence of real tool calls, emit a partial, then drive the
    iteration callback so the budget governor can raise BudgetBreach."""

    protocol = "anthropic"

    def __init__(self, calls: list[tuple[str, dict[str, object]]], partial: str) -> None:
        self._calls = calls
        self._partial = partial

    async def complete_with_tools(  # noqa: ANN001
        self,
        *,
        user_text: str,
        system_text: str,
        tool_schemas: list[dict[str, object]],
        tool_dispatcher: Any,
        history: list[Any] | None = None,
        on_iteration_complete: Any = None,
        **_kwargs: object,
    ) -> tuple[str, list[dict[str, Any]]]:
        all_calls: list[dict[str, Any]] = []
        # Dispatch every scripted tool through the REAL _dispatch (records outcomes).
        for name, args in self._calls:
            rendered = await tool_dispatcher(name, args)
            all_calls.append({"name": name, "args": args, "result": rendered})
        # Emit the forward-looking overclaim as the assistant's partial text, then
        # signal an iteration so the governor can fire at the squeezed cap.
        messages = [{"role": "assistant", "content": self._partial}]
        if on_iteration_complete is not None:
            await on_iteration_complete(
                ReActIterationState(iteration=0, messages=messages, tool_call_records=all_calls),
            )
            # A second iteration: with max_steps=1 the callback raises here (steps_done=2).
            await on_iteration_complete(
                ReActIterationState(iteration=1, messages=messages, tool_call_records=all_calls),
            )
        return (self._partial, all_calls)

    async def complete(self, messages: list, model: str, **kwargs: object):  # noqa: ANN201
        from stackowl.providers.base import CompletionResult
        return CompletionResult(
            content="x", input_tokens=1, output_tokens=1,
            model="m", provider_name=_OWL_NAME, duration_ms=1.0,
        )

    async def stream(self, *a: Any, **k: Any):  # pragma: no cover
        if False:  # noqa: SIM210
            yield ""


class _FakeProviderRegistry:
    def __init__(self, p: _ScriptedProvider) -> None:
        self._p = p

    def get(self, name: str) -> _ScriptedProvider:
        return self._p

    def get_by_tier(self, tier: str) -> _ScriptedProvider:
        return self._p

    def get_with_cascade(self, preferred_tier: str) -> _ScriptedProvider:
        return self._p


def _owl(tool_names: set[str]) -> OwlAgentManifest:
    # max_steps=1 → BudgetBreach fires at the 2nd iteration WITHOUT any sleep.
    return OwlAgentManifest(
        name=_OWL_NAME,
        role="pictures",
        system_prompt="Help with pictures.",
        model_tier="fast",
        bounds=BoundsSpec(tools=frozenset(tool_names), caps=ResourceCaps(max_steps=1)),
    )


async def _drive(
    tools: list[Tool],
    calls: list[tuple[str, dict[str, object]]],
    partial: str,
) -> PipelineState:
    """Run _run_with_tools (catches BudgetBreach, stamps snapshot) then the backend's
    exact pre-delivery floor step. Returns the post-floor state."""
    registry = ToolRegistry()
    for t in tools:
        registry.register(t)
    owl_registry = OwlRegistry()
    owl_registry.register(_owl({t.name for t in tools}))
    provider = _ScriptedProvider(calls, partial)

    state = PipelineState(
        trace_id="t-pics", session_id="s-pics", input_text="can you help me with pictures",
        channel="telegram", owl_name=_OWL_NAME, pipeline_step="execute", interactive=False,
    )
    token = set_services(StepServices(
        provider_registry=_FakeProviderRegistry(provider),  # type: ignore[arg-type]
        tool_registry=registry,
        owl_registry=owl_registry,
        # Grant consent so the consequential delivery tool actually RUNS and records
        # an effectful failure (the incident: a real failed consequential action, not
        # a consent-denied no-op).
        consent_gate=ConsequentialActionGate(confirm_fn=lambda _name: True),
        stream_registry=StreamRegistry(),
        cost_tracker=None,
    ))
    ledger_token = tool_outcome_ledger.bind()
    recovery_token = recovery_context.bind()
    try:
        out = await _run_with_tools(state, provider, registry)  # type: ignore[arg-type]
        # The backend runs this immediately after execute, pre-delivery.
        return await surface_consequential_giveup_floor(out)
    finally:
        recovery_context.reset(recovery_token)
        tool_outcome_ledger.reset(ledger_token)
        reset_services(token)


# ===========================================================================
# THE INCIDENT — budget cap + failed consequential + incidental write success.
# ===========================================================================


async def test_budget_cap_incidental_success_ships_honest_floor() -> None:
    """The overclaim must be REPLACED by the honest floor (is_floor=True)."""
    out = await _drive(
        tools=[_FailingSendImageTool(), _IncidentalWriteTool()],
        calls=[("send_image", {"path": "diagram.svg"}), ("write_file", {"path": "diagram.svg"})],
        partial=_OVERCLAIM,
    )
    delivered = "".join(c.content for c in out.responses)
    # The budget cap really fired (the loop was cut off).
    assert any("budget" in e for e in out.errors), (
        f"expected a budget-stop marker in errors; got {out.errors}"
    )
    # The overclaim is GONE — replaced by an honest floor.
    assert "gorgeous on your phone" not in delivered, (
        f"OVERCLAIM SHIPPED: the dressed-up partial reached the user. delivered={delivered!r}"
    )
    assert any(getattr(c, "is_floor", False) for c in out.responses), (
        f"expected an is_floor=True honest floor chunk; got {out.responses!r}"
    )
    assert delivered.strip(), "floor must be non-empty"


# ===========================================================================
# FALSIFICATION GUARD (a) — a GOAL-RELEVANT delivered success is NOT floored.
# ===========================================================================


async def test_budget_cap_with_delivered_artifact_is_not_floored() -> None:
    """A consequential success (delivered OUT to the user) at the cap keeps its partial."""
    out = await _drive(
        tools=[_DeliveredSendTool()],
        calls=[("send_image", {"path": "diagram.svg"})],
        partial="Here is your picture!",
    )
    delivered = "".join(c.content for c in out.responses)
    assert "Here is your picture!" in delivered, (
        f"a turn that DELIVERED a goal-relevant artifact was floored. delivered={delivered!r}"
    )
    assert not any(getattr(c, "is_floor", False) for c in out.responses), (
        "a delivered turn must not carry an is_floor honest floor"
    )


# ===========================================================================
# FALSIFICATION GUARD (b) — a WRITE-severity boundary-crossing dispatch
# (delegate_task) that SUCCEEDS is delivered work; a turn that genuinely dispatched
# delegated work must NOT be floored at the budget cap even when a consequential
# tool also FAILED. (The original consequential-only `delivered` would have WRONGLY
# floored this — delegate_task is action_severity="write", not "consequential".)
# ===========================================================================


async def test_budget_cap_delegated_dispatch_is_not_floored_despite_consequential_failure() -> None:
    """delegate_task SUCCEEDS + send_image FAILS at the cap → partial KEPT, no floor.

    Real delegated work crossed the boundary OUT, so the turn is not a give-up. This is
    the FIX-1 falsification guard: the goal-relevant subset must include boundary-crossing
    `write` dispatches (delegate_task / sessions_*), not just `consequential` sends."""
    out = await _drive(
        tools=[_FailingSendImageTool(), _DelegateTaskTool()],
        calls=[
            ("delegate_task", {"task": "render the diagrams"}),
            ("send_image", {"path": "diagram.svg"}),
        ],
        partial="I've dispatched the diagram work to a specialist and will follow up.",
    )
    delivered = "".join(c.content for c in out.responses)
    # The budget cap really fired.
    assert any("budget" in e for e in out.errors), (
        f"expected a budget-stop marker in errors; got {out.errors}"
    )
    # Delegated work crossed the boundary → NOT floored, partial kept.
    assert "dispatched the diagram work" in delivered, (
        f"a turn that genuinely dispatched delegated work was floored. delivered={delivered!r}"
    )
    assert not any(getattr(c, "is_floor", False) for c in out.responses), (
        "a delegated-dispatch turn must not carry an is_floor honest floor"
    )


# ===========================================================================
# FIX-4 — NON-CAP INVARIANT LOCK. A clean (NOT budget-capped) terminal snapshot
# with an incidental write_file success masking a consequential failure must read
# the FULL `consequential_successes` tally (byte-identical to pre-change main:
# cs=1 → NOT a give-up). The budget-cap path reads `delivered_successes` (cs=0 →
# floored). This locks the gating so a future flip of the `budget_capped` default
# is caught.
# ===========================================================================


def test_non_cap_path_reads_full_success_tally_byte_identical() -> None:
    """is_consequential_giveup_now is gated on state.budget_capped.

    Same snapshot (1 consequential failure, incidental write_file success counted in
    consequential_successes, delivered_successes empty):
      * budget_capped=False  → reads consequential_successes (cs=1) → NOT a give-up
                               (byte-identical to pre-change main).
      * budget_capped=True   → reads delivered_successes (cs=0)     → give-up (floored).
    """
    from stackowl.pipeline.delivery_gate import is_consequential_giveup_now

    base_kwargs: dict[str, Any] = dict(
        trace_id="t", session_id="s", input_text="x", channel="cli",
        owl_name=_OWL_NAME, pipeline_step="execute", interactive=False,
        consequential_snapshot_taken=True,
        consequential_failures=("send_image",),
        consequential_successes=("write_file",),  # incidental write counted here
        delivered_successes=(),                   # nothing delivered OUT
        recovered_consequential=(),
    )

    clean = PipelineState(**base_kwargs, budget_capped=False)
    assert is_consequential_giveup_now(clean) is False, (
        "NON-cap path must read the full consequential_successes tally (cs=1) and NOT "
        "floor — byte-identical to pre-change behavior"
    )

    capped = PipelineState(**base_kwargs, budget_capped=True)
    assert is_consequential_giveup_now(capped) is True, (
        "budget-cap path must read delivered_successes (cs=0) and floor the masked "
        "consequential failure"
    )


# ===========================================================================
# FALSIFICATION GUARD (c) — the nudge-veto predicate is byte-identical.
# A clean (non-budget) turn with an incidental write success and NO consequential
# failure must NOT be a give-up — no regression to legitimate "save a file" turns.
# ===========================================================================


def test_nudge_veto_predicate_unchanged_for_incidental_write_success() -> None:
    """is_unachieved_consequential_giveup keeps its meaning for the nudge path:
    one effectful success and zero failures is NOT a give-up."""
    from stackowl.pipeline.persistence import is_unachieved_consequential_giveup

    # write_file succeeded, nothing failed → not a give-up (legitimate save-a-file).
    assert is_unachieved_consequential_giveup(cons_failures=0, cons_successes=1) is False
    # A failure with NO success is still a give-up (unchanged).
    assert is_unachieved_consequential_giveup(cons_failures=1, cons_successes=0) is True
    # The classic "incidental success masks a failure" shape — the SHARED predicate
    # still reads it as NOT-a-giveup (the goal-relevance fix lives at the terminal
    # budget-cap path, NOT in this shared nudge predicate).
    assert is_unachieved_consequential_giveup(cons_failures=1, cons_successes=1) is False
