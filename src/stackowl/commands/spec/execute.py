"""``execute_command_task`` -- runs one claimed COMMAND row (Story 4.3, AD-1/AD-26).

The ``TaskLoop`` runner's ``kind == "command"`` branch target
(``pipeline/durable/task_loop_runner.py``), and also called directly by
``submit.py::submit_command`` for its inline (non-tick) execution path — ONE
function, two callers, mirroring ``task_loop_runner.py::actuator_row_for``'s
own "one builder, two callers" shape.

Execution order is fixed (AD-1): severity check via ``principal_for`` FIRST,
then the deterministic handler from ``CommandHandlerRegistry`` — no model
call, ever. No generic dispatch: the command type names the ``CommandSpec``
and the handler, both resolved from closed, explicit dicts.

PURPOSELY DOES NO DATABASE OR JOURNAL I/O. AD-7 restricts this package's
imports to ``authz/`` + ``pipeline/durable`` (+ stdlib/pydantic) — never
``journal`` directly. Recording ``command.enqueued``/``command.completed`` is
therefore ``pipeline/durable/store.py``'s job (its ``create``/``mark_delivered``
already own the ``tasks`` row's lifecycle bookkeeping); this function only
decides WHETHER a command may run and WHAT running it produced, and returns
that decision to a caller that already holds a store.
"""

from __future__ import annotations

from stackowl.authz.requester import principal_for
from stackowl.commands.spec.context import CommandContext, CommandOutcome
from stackowl.commands.spec.errors import CommandRefusedError
from stackowl.commands.spec.handlers import CommandHandlerRegistry
from stackowl.commands.spec.registry import CommandSpecRegistry

# AD-7 — see registry.py's identical import for why `log` is allowed here.
from stackowl.infra.observability import log
from stackowl.pipeline.durable.task import DurableTask


async def execute_command_task(task: DurableTask) -> CommandOutcome:
    """Run *task* (a claimed ``kind='command'`` row) through the one door.

    Never itself updates ``task``'s row status or records a journal event —
    the caller (the ``TaskLoop`` dispatch loop for the tick path,
    ``submit_command`` for the inline path) owns that via the store, exactly
    as ``task_loop_runner.py``'s existing goal-task runner never calls
    ``store.mark_delivered``/``fail_and_requeue`` itself either.

    Raises :class:`~stackowl.commands.spec.errors.CommandTypeNotDeclaredError`
    when ``task.command_type`` names nothing in :class:`CommandSpecRegistry`/
    :class:`CommandHandlerRegistry`, and :class:`~stackowl.commands.spec.
    errors.CommandRefusedError` when the severity check refuses (see this
    module's docstring: not exercised by any live surface today, since
    ``principal_for`` currently grants every severity).
    """
    command_type = task.command_type or ""
    command_id = task.command_id or task.task_id
    # 1. ENTRY
    log.tasks.debug(
        "[commands] execute.execute_command_task: entry",
        extra={"_fields": {
            "task_id": task.task_id, "command_type": command_type,
            "command_id": command_id,
        }},
    )
    spec = CommandSpecRegistry.get(command_type)
    payload = spec.payload_model.model_validate_json(task.command_payload or "{}")
    requester_kind = task.requester_kind or "owner"
    context = CommandContext(
        command_id=command_id, command_type=command_type,
        requester_kind=requester_kind, utterance_id=task.utterance_id,
    )
    # 2. DECISION — the severity check runs BEFORE the handler, unconditionally
    # (AD-1: "no preview/dry-run returns before the severity check").
    principal = principal_for(requester_kind)  # type: ignore[arg-type]
    if not principal.may(spec.severity):
        log.tasks.warning(
            "[commands] execute.execute_command_task: severity check refused",
            extra={"_fields": {
                "command_type": command_type, "severity": spec.severity,
                "requester_kind": requester_kind,
            }},
        )
        raise CommandRefusedError(command_type, spec.severity, requester_kind)
    handler = CommandHandlerRegistry.get(command_type)
    # 3. STEP — the deterministic handler. No model call, ever (AD-26).
    outcome = await handler(payload, context)
    # 4. EXIT
    log.tasks.info(
        "[commands] execute.execute_command_task: exit",
        extra={"_fields": {
            "task_id": task.task_id, "command_type": command_type,
            "success": outcome.success,
        }},
    )
    return outcome
