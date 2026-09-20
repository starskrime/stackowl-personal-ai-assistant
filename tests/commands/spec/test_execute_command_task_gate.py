"""Story 4.4 -- the action-policy gate wired into ``execute_command_task``.

The ``run_at_once`` path stays byte-identical to Story 4.3 (severity check ->
handler, no gate detour); a non-immediate decision raises
``CommandNeedsDecisionError`` instead of running the handler; a row whose
``gate_verdict`` already reads ``"approved"`` (the resumed-after-answer path,
Story 4.4's own store methods) skips the gate entirely.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict, Field

from stackowl.commands.spec.command_spec import CommandSpec
from stackowl.commands.spec.context import CommandContext, CommandOutcome
from stackowl.commands.spec.errors import CommandNeedsDecisionError
from stackowl.commands.spec.execute import execute_command_task
from stackowl.commands.spec.handlers import CommandHandlerRegistry
from stackowl.commands.spec.registry import CommandSpecRegistry
from stackowl.pipeline.durable.task import DurableTask

pytestmark = pytest.mark.asyncio

_TYPE = "widget.gate_probe"


class _Payload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    widget_id: str = Field(min_length=1)


@pytest.fixture(autouse=True)
def _isolate_registries() -> Any:
    """Snapshot-and-restore — mirrors ``test_submit_command.py``'s own
    fixture: these are process-wide singletons."""
    spec_snapshot = dict(CommandSpecRegistry._specs)  # noqa: SLF001
    handler_snapshot = dict(CommandHandlerRegistry._handlers)  # noqa: SLF001
    CommandSpecRegistry.reset()
    CommandHandlerRegistry.reset()
    yield
    CommandSpecRegistry.reset()
    CommandHandlerRegistry.reset()
    CommandSpecRegistry._specs.update(spec_snapshot)  # noqa: SLF001
    CommandHandlerRegistry._handlers.update(handler_snapshot)  # noqa: SLF001


def _register(*, severity: str, reversible: bool, ran: list[str]) -> None:
    CommandSpecRegistry.register(CommandSpec(
        command_type=_TYPE, payload_model=_Payload, severity=severity,
        reversible=reversible,
        undo_command_type=("widget.gate_probe.undo" if reversible else None),
    ))

    async def _handler(payload: _Payload, context: CommandContext) -> CommandOutcome:
        ran.append(context.command_id)
        return CommandOutcome(success=True, result={"widget_id": payload.widget_id})

    CommandHandlerRegistry.register(_TYPE, _handler)  # type: ignore[arg-type]


def _task(
    *, requester_kind: str = "owner", gate_verdict: str | None = None,
) -> DurableTask:
    return DurableTask(
        task_id="cmd-t1", goal=f"command:{_TYPE}", status="running", kind="command",
        command_type=_TYPE, command_payload=json.dumps({"widget_id": "w1"}),
        command_id="c1", requester_kind=requester_kind, gate_verdict=gate_verdict,
    )


async def test_run_at_once_stays_byte_identical_to_story_4_3() -> None:
    """Owner, reversible, WRITE -- the handler runs, no signal raised."""
    ran: list[str] = []
    _register(severity="write", reversible=True, ran=ran)
    outcome = await execute_command_task(_task())
    assert outcome.success is True
    assert ran == ["c1"]


async def test_a_non_immediate_decision_raises_instead_of_running_the_handler() -> None:
    """Owner, irreversible, WRITE -- needs_step_up; the handler never runs."""
    ran: list[str] = []
    _register(severity="write", reversible=False, ran=ran)
    with pytest.raises(CommandNeedsDecisionError) as exc_info:
        await execute_command_task(_task())
    assert exc_info.value.command_type == _TYPE
    assert exc_info.value.outcome == "needs_step_up"
    assert ran == []


async def test_an_owl_request_always_needs_approval_even_when_reversible() -> None:
    ran: list[str] = []
    _register(severity="write", reversible=True, ran=ran)
    with pytest.raises(CommandNeedsDecisionError) as exc_info:
        await execute_command_task(_task(requester_kind="owl"))
    assert exc_info.value.outcome == "needs_approval"
    assert ran == []


async def test_the_payload_summary_is_deterministic_json_never_a_model() -> None:
    ran: list[str] = []
    _register(severity="consequential", reversible=True, ran=ran)
    with pytest.raises(CommandNeedsDecisionError) as exc_info:
        await execute_command_task(_task())
    summary = json.loads(exc_info.value.payload_summary)
    assert summary == {"widget_id": "w1"}
    assert len(exc_info.value.payload_summary) <= 256


async def test_gate_verdict_approved_skips_the_gate_and_runs_the_handler() -> None:
    """The resumed-after-answer path (Story 4.4's own
    ``resume_command_after_answer``): an otherwise irreversible command whose
    row already carries ``gate_verdict='approved'`` skips straight to the
    handler -- no re-decision, no second raise."""
    ran: list[str] = []
    _register(severity="write", reversible=False, ran=ran)
    outcome = await execute_command_task(_task(gate_verdict="approved"))
    assert outcome.success is True
    assert ran == ["c1"]


async def test_voice_unverified_consequential_needs_step_up_not_run_at_once() -> None:
    """FR41: a later spoken 'yes' cannot resolve it — proven here by the gate
    refusing to run at once for a CONSEQUENTIAL command even though
    ``voice-unverified`` runs reversible WRITEs at once."""
    ran: list[str] = []
    _register(severity="consequential", reversible=True, ran=ran)
    with pytest.raises(CommandNeedsDecisionError) as exc_info:
        await execute_command_task(_task(requester_kind="voice-unverified"))
    assert exc_info.value.outcome == "needs_step_up"
    assert ran == []
