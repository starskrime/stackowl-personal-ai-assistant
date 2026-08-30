"""Loop-guard guidance must reach the model on the turns that are actually looping.

THE DEFECT, live at the time of writing. execute.py computes `_guard_note` from
`guardrails.after_call(...)` for EVERY completed dispatch, and then appends it to
exactly one return:

    if is_trustworthy_success(tr.success, tr.verified):
        return tr.output + _guard_note        # <- the only place it is used

Every failure exit — honest surrender, collapsed repeat, plain error — drops it.

WHY THAT IS BACKWARDS. `ToolGuardrails._record_failure` produces warnings ONLY on
failure — and those are exactly the ones that never reach the model, while the
success-side warnings (exact repeat, idempotent no-progress) do.

WHAT THE FIRST RUN OF THIS TEST CORRECTED, and it narrows the claim. A repeat with
IDENTICAL arguments never reaches `_guarded_dispatch` at all: an earlier
already-tried cache short-circuits it with its own, stronger message ("You already
tried 'dead_fetch' with these exact inputs earlier this turn and it failed: …").
So `repeated_exact_failure_warning` is largely moot in production.

The case that IS live and IS dropped is `same_tool_failure_warning`: one tool failing
three or more times with DIFFERENT arguments — five different URLs all returning 502,
four different shell commands all erroring. Nothing short-circuits that (the
arguments differ every time, so the cache never hits), the detector fires, it logs
`[guardrails] tool-loop warning` at INFO, and the model is told nothing. That is the
shape that burns a step budget, and it is what this test pins.
"""

from __future__ import annotations

from typing import Any

import pytest
from tests.pipeline.test_default_backstop_no_marker import _make_services

from stackowl.pipeline.services import reset_services, set_services
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps.execute import _run_with_tools
from stackowl.tools.base import Tool, ToolManifest, ToolResult


class _AlwaysFailsTool(Tool):
    """A read tool that deterministically fails — the classic stuck-loop shape."""

    @property
    def name(self) -> str:
        return "dead_fetch"

    @property
    def description(self) -> str:
        return "Fetches a URL that is not coming back."

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {"url": {"type": "string"}}}

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name="dead_fetch", description=self.description,
            parameters=self.parameters, action_severity="read",
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        return ToolResult(
            success=False, output="", error="HTTP 502 from host", duration_ms=1.0,
        )


class _RepeatingProvider:
    """Calls the same failing tool with the SAME arguments, N times."""

    protocol = "anthropic"

    def __init__(self, calls: int = 5, *, vary_args: bool = True) -> None:
        self.calls = calls
        self.vary_args = vary_args
        self.observations: list[str] = []

    async def complete_with_tools(
        self, *, user_text: str, system_text: str,
        tool_schemas: list[dict[str, object]], tool_dispatcher: Any,
        history: list[Any] | None = None, **_kwargs: object,
    ) -> tuple[str, list[dict[str, Any]]]:
        for i in range(self.calls):
            # DIFFERENT arguments each round — the live shape. Identical arguments
            # are short-circuited upstream by the already-tried cache and never
            # reach the guardrail path this test is about.
            url = f"https://dead{i}.example" if self.vary_args else "https://dead.example"
            obs = await tool_dispatcher("dead_fetch", {"url": url})
            self.observations.append(str(obs))
        return ("gave up", [])


async def _run(provider: _RepeatingProvider) -> None:
    services = _make_services("guard_owl")
    services.tool_registry.register(_AlwaysFailsTool())  # type: ignore[attr-defined]
    state = PipelineState(
        trace_id="trace-guard", session_key="sess-guard",
        input_text="fetch that page", channel="cli",
        owl_name="guard_owl", pipeline_step="execute", interactive=False,
    )
    token = set_services(services)
    try:
        await _run_with_tools(state, provider, services.tool_registry)
    finally:
        reset_services(token)


@pytest.mark.asyncio
async def test_a_repeating_FAILURE_tells_the_model_it_is_looping() -> None:
    """The bug. Five failing calls with different arguments, model told nothing."""
    provider = _RepeatingProvider(calls=5)
    await _run(provider)

    assert provider.observations, "the dispatcher was never exercised"
    noted = [o for o in provider.observations if "[loop-guard]" in o]
    assert noted, (
        "five FAILING calls produced no loop-guard guidance to the model. "
        "The detector fired and the note was discarded on the failure branch:\n"
        + "\n---\n".join(provider.observations)
    )


@pytest.mark.asyncio
async def test_the_note_still_carries_the_failure_marker() -> None:
    """Guidance must not launder a failure into something that reads like success.

    consolidate.py's merge filter and the give-up judge both key on the marker; if
    appending the note dropped it, a failed call could ship as the answer.
    """
    from stackowl.pipeline.persistence import TOOL_FAILED_MARKER

    provider = _RepeatingProvider(calls=5)
    await _run(provider)
    noted = [o for o in provider.observations if "[loop-guard]" in o]
    assert noted
    assert all(TOOL_FAILED_MARKER in o for o in noted), (
        "the loop-guard note replaced or hid the failure marker"
    )


@pytest.mark.asyncio
async def test_a_single_failure_gets_NO_note() -> None:
    """The guard must stay quiet below its threshold — one failure is not a loop."""
    provider = _RepeatingProvider(calls=1)
    await _run(provider)
    assert not any("[loop-guard]" in o for o in provider.observations)


@pytest.mark.asyncio
async def test_the_identical_argument_case_is_handled_UPSTREAM() -> None:
    """Records the mechanism that already exists, so it is not rebuilt here.

    An identical repeat is intercepted before the tool runs and answered with the
    already-tried message. This is stronger than the guard note (it skips the call
    entirely) and is why the exact-repeat warning is not the live gap.
    """
    provider = _RepeatingProvider(calls=3, vary_args=False)
    await _run(provider)
    assert any("already tried" in o.lower() for o in provider.observations), (
        "the already-tried short-circuit regressed:\n"
        + "\n---\n".join(provider.observations)
    )
