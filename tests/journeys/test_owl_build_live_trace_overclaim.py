"""GATEWAY JOURNEY — Task 0 (FR-11/12 Phase B): does the overclaim gate already
block the live user complaint, or did a false "owl created" success reach them?

Live trace ``6f9d6ed79ce7444f84b22f9c7af0d750`` (2026-07-01,
``~/.stackowl/logs/stackowl-2026-07-01.jsonl``) shows the REAL ``owl_build`` tool
rejecting two malformed payloads the weak model emitted in a single turn:

1. Extra fields (``prompt``, ``priority``) alongside an otherwise-valid create spec
   -> ``OwlBuildSpec`` (``extra="forbid"``) raises 2 ``extra_forbidden`` errors.
2. ``explicit_tools`` sent as the JSON STRING ``'["memory", "owl_build"]'`` instead
   of a real list -> ``OwlBuildSpec`` raises a ``list_type`` error.

Both go through ``OwlBuildTool._err`` (``tools/meta/owl_build.py``), which returns
``ToolResult(success=False, verified=None, ...)`` — it never sets ``verified=True``
or clears the tool's declared ``effect_class``. ``owl_build``'s manifest declares
``effect_class="creates_persistent_entity"`` UNCONDITIONALLY (``owl_build.py``
``.manifest``), and ``record_tool_outcome`` (``execute.py`` ~1328-1339) records that
effect_class off the tool's manifest regardless of whether the call succeeded. So
``state.unverified_effects`` (``execute.py`` ~778-781, keyed on
``o.effect_class is not None and o.verified is not True``) SHOULD be non-empty for
this turn, and ``surface_overclaim_gate`` SHOULD replace a confident "created!" draft
with the honest floor.

This test drives the REAL pipeline — real ``OwlBuildTool``, real ``_run_with_tools``,
real ledger — with a scripted provider that replays both malformed payloads, then
asserts on the FINAL emitted response after both gates run. The AI provider is the
ONLY mock (mirrors ``test_overclaim_gate_journey.py``).
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
    surface_consequential_giveup_floor,
    surface_overclaim_gate,
)
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps.execute import _run_with_tools, _snapshot_consequential
from stackowl.pipeline.streaming import StreamRegistry
from stackowl.tools.meta.owl_build import OwlBuildTool
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

_OWL = "live_trace_owl"
_FALSE_SUCCESS_TEXT = "Done! I've created your new owl and it's all set up and ready to go."

# The two exact malformed payloads replayed verbatim (shape) from live trace
# 6f9d6ed79ce7444f84b22f9c7af0d750 -- see owl_build.execute: malformed spec log
# entries for that trace_id.
_PAYLOAD_1_EXTRA_FIELDS: dict[str, object] = {
    "action": "create",
    "name": "reminder_owl",
    "schedule": "every 2h",
    "prompt": "Check memory for any \"go ahead\" replies and send the notification.",
    "priority": "medium",
}
_PAYLOAD_2_JSON_STRING_LIST: dict[str, object] = {
    "action": "create",
    "name": "reminder_owl",
    "schedule": "every 2h",
    "specialty": "reminds the user about pending approvals",
    "goal": "check memory and notify",
    "explicit_tools": '["memory", "owl_build"]',
}


@pytest.fixture(autouse=True)
def _live_io():  # noqa: ANN202
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]
    yield
    TestModeGuard._active = prev  # type: ignore[attr-defined]


class _ReplayLiveTraceProvider:
    """Replays the two malformed owl_build payloads from the live trace, then
    emits a confident false-success claim -- exactly the shape of the user's
    live complaint (assistant said it worked; nothing was actually created)."""

    protocol = "anthropic"

    async def complete_with_tools(  # noqa: ANN001
        self, *, user_text, system_text, tool_schemas, tool_dispatcher,
        history=None, on_iteration_complete=None, **_kwargs,
    ):
        records: list[dict[str, Any]] = []
        for payload in (_PAYLOAD_1_EXTRA_FIELDS, _PAYLOAD_2_JSON_STRING_LIST):
            out = await tool_dispatcher("owl_build", payload)
            records.append({"name": "owl_build", "args": payload, "result": out})
        return (_FALSE_SUCCESS_TEXT, records)

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


async def _drive_live_trace_turn() -> PipelineState:
    """Run the REAL pipeline (owl_build tool, ledger, both gates) for a single
    turn replaying the two live-trace malformed payloads, and return the FINAL
    state after surface_consequential_giveup_floor + surface_overclaim_gate --
    i.e. what would actually be delivered to the user."""
    tool = OwlBuildTool()
    registry = ToolRegistry()
    registry.register(tool)
    owl_registry = OwlRegistry()
    owl_registry.register(OwlAgentManifest(
        name=_OWL, role="t", system_prompt="t", model_tier="fast",
        bounds=BoundsSpec(
            tools=frozenset({tool.name}),
            caps=ResourceCaps(max_steps=50),
        ),
    ))
    state = PipelineState(
        trace_id="t-live-6f9d6ed7", session_id="s", input_text="make me a new owl",
        channel="telegram", owl_name=_OWL, pipeline_step="execute", interactive=False,
    )
    svc_token = set_services(StepServices(
        provider_registry=_Reg(_ReplayLiveTraceProvider()),  # type: ignore[arg-type]
        tool_registry=registry, owl_registry=owl_registry,
        consent_gate=ConsequentialActionGate(confirm_fn=lambda _n: True),
        stream_registry=StreamRegistry(), cost_tracker=None,
    ))
    ledger_token = tool_outcome_ledger.bind()
    recovery_token = recovery_context.bind()
    try:
        after_execute = await _run_with_tools(
            state, _ReplayLiveTraceProvider(), registry  # type: ignore[arg-type]
        )
        snapshotted = (
            _snapshot_consequential(after_execute)
            if not after_execute.consequential_snapshot_taken
            else after_execute
        )
        after_floor = await surface_consequential_giveup_floor(snapshotted)
        return await surface_overclaim_gate(after_floor)
    finally:
        recovery_context.reset(recovery_token)
        tool_outcome_ledger.reset(ledger_token)
        reset_services(svc_token)


@pytest.mark.asyncio
async def test_live_trace_malformed_owl_build_never_reaches_user_as_success() -> None:
    """Task 0 verdict test: replay the two live-trace malformed payloads through
    the REAL pipeline and assert on the FINAL emitted response.

    VERDICT (as of this test passing): unverified_effects DOES get stamped with
    'owl_build' for both malformed calls. In THIS scenario (two malformed calls,
    nothing delivered) it is actually ``surface_consequential_giveup_floor`` --
    the EARLIER honesty gate -- that fires first (consequential_failures=
    ('owl_build', 'owl_build'), delivered_successes=()), so
    ``surface_overclaim_gate`` sees an already-floored draft and clears (its
    is_floor guard). Either gate flooring is a correct outcome for this pass: the
    live-user-visible incident (false "owl created" success reaching the user) is
    NOT reproduced by this exact trace/mechanism -- see Item 2 in the dev notes
    for what this determines.
    """
    final_state = await _drive_live_trace_turn()

    final_text = "".join(c.content for c in final_state.responses)

    # The false "created" claim must never reach the user.
    assert _FALSE_SUCCESS_TEXT not in final_text, (
        "REGRESSION: a false owl_build success claim reached the final response "
        f"unblocked. Got: {final_text!r}"
    )
    # Some honesty gate must have fired and floored the response (either the
    # give-up floor or the overclaim gate -- both are correct outcomes here).
    assert any(getattr(c, "is_floor", False) for c in final_state.responses), (
        f"Expected an honest is_floor=True chunk; got: {final_state.responses!r}"
    )
    assert final_state.unverified_effects == ("owl_build", "owl_build"), (
        "Expected both malformed owl_build calls to land in unverified_effects "
        f"(ADR-T2/TS3 MEASURED veto); got: {final_state.unverified_effects!r}"
    )
    assert final_text.strip(), "Honest floor must not be empty"
