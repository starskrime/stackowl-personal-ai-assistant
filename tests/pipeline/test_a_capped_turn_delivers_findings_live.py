"""The salvage path through `_run_with_tools` — the path production actually takes.

The unit tests in ``tests/pipeline/budget/`` pin ``build_salvage_messages`` and
``summarize_findings``. Neither would have caught the real defect, because the real
defect is at a CALL SITE: execute.py held twenty tool results and passed
``attempts=[]`` to the synthesizer.

The pre-existing budget suites all stayed green after the fix — every one of them
scripts a provider with an EMPTY ``tool_call_records`` list, so salvage correctly
declines and the floor renders exactly as before. That is a real regression guarantee
and a vacuous test of the new behaviour. This file supplies the missing case: a turn
that breaches WITH findings and WITHOUT partial text — trace ``f33c9fa0``'s shape.
"""

from __future__ import annotations

from typing import Any

import pytest
from tests.pipeline.test_default_backstop_no_marker import (
    _SCRIPTED_ITERATIONS_DEFAULT,
    _make_services,
)

from stackowl.pipeline.services import reset_services, set_services
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps.execute import _run_with_tools
from stackowl.providers.react_callback import ReActIterationState


class _FindingProvider:
    """Breaches the default backstop with findings but NO assistant text.

    This is the measured shape: every round spent on tool calls, so
    ``_last_assistant_text`` returns "" and the old code delivered only the note.
    """

    protocol = "anthropic"

    def __init__(self, *, summary: str = "You have 3 owls: brain, mailbutler, rca.",
                 complete_raises: bool = False) -> None:
        self.summary = summary
        self.complete_raises = complete_raises
        self.complete_calls: list[list[Any]] = []

    async def complete_with_tools(
        self, *, user_text: str, system_text: str,
        tool_schemas: list[dict[str, object]], tool_dispatcher: Any,
        history: list[Any] | None = None, on_iteration_complete: Any = None,
        **_kwargs: object,
    ) -> tuple[str, list[dict[str, Any]]]:
        all_calls: list[dict[str, Any]] = []
        for i in range(_SCRIPTED_ITERATIONS_DEFAULT):
            all_calls.append({
                "name": "owl_list",
                "args": {},
                "result": "brain, mailbutler, rca_gatherer",
                "failed": False,
            })
            if on_iteration_complete is not None:
                await on_iteration_complete(ReActIterationState(
                    iteration=i, messages=[], tool_call_records=list(all_calls),
                ))
        return ("", all_calls)

    async def complete(self, messages, model="", **kwargs):  # noqa: ANN001,ANN003
        self.complete_calls.append(messages)
        if self.complete_raises:
            raise RuntimeError("provider down")

        class _R:
            content = self.summary
        return _R()


def _state() -> PipelineState:
    return PipelineState(
        trace_id="trace-salvage", session_key="sess-salvage",
        input_text="What agents do I have", channel="cli",
        owl_name="salvage_owl", pipeline_step="execute", interactive=False,
    )


async def _run(provider: _FindingProvider) -> PipelineState:
    services = _make_services("salvage_owl")
    token = set_services(services)
    try:
        return await _run_with_tools(_state(), provider, services.tool_registry)
    finally:
        reset_services(token)


@pytest.mark.asyncio
async def test_the_capped_turn_delivers_the_FINDINGS_not_just_an_apology() -> None:
    """The defect Bakir reported, end to end."""
    provider = _FindingProvider()
    out = await _run(provider)

    delivered = "\n".join(c.content for c in out.responses)
    assert delivered.strip(), "never-empty invariant broken"
    assert "brain, mailbutler, rca" in delivered, (
        "the turn held the answer in its tool results and delivered only the stop "
        f"note — the exact f33c9fa0 defect:\n{delivered}"
    )
    assert provider.complete_calls, "salvage never called the provider"
    assert out.budget_capped is True, "the turn must still be recorded as capped"


@pytest.mark.asyncio
async def test_it_is_still_HONEST_that_the_turn_was_cut_short() -> None:
    """Delivering findings must not disguise an unfinished turn as a finished one.

    This is the honesty rule the backstop note was added for on 2026-08-24; salvage
    adds an answer in front of it, it does not replace it.
    """
    out = await _run(_FindingProvider())
    delivered = "\n".join(c.content for c in out.responses)
    assert "ran out of steps" in delivered, (
        f"the turn silently presented partial work as complete:\n{delivered}"
    )


@pytest.mark.asyncio
async def test_a_salvage_failure_falls_back_to_the_floor_and_never_crashes() -> None:
    """Salvage runs on the way out of an already-failed turn.

    If it raised, it would convert a degraded answer into a crash — strictly worse
    than the silence it exists to fix.
    """
    out = await _run(_FindingProvider(complete_raises=True))
    delivered = "\n".join(c.content for c in out.responses)
    assert delivered.strip(), "the never-empty floor did not hold when salvage failed"
    assert "brain" not in delivered, "a failed salvage must not fabricate findings"


@pytest.mark.asyncio
async def test_the_floor_fallback_now_names_what_was_TRIED() -> None:
    """Even with no summary, `attempts=[]` was a lie — the turn tried 21 times."""
    out = await _run(_FindingProvider(summary="   "))
    delivered = "\n".join(c.content for c in out.responses)
    assert delivered.strip()
    assert "owl_list" in delivered, (
        f"the floor still reports no attempts on a turn that made 21:\n{delivered}"
    )
