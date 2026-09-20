"""``execute_command_task`` -- runs one claimed COMMAND row (Story 4.3, AD-1/AD-26).

The ``TaskLoop`` runner's ``kind == "command"`` branch target
(``pipeline/durable/task_loop_runner.py``), and also called directly by
``submit.py::submit_command`` for its inline (non-tick) execution path — ONE
function, two callers, mirroring ``task_loop_runner.py::actuator_row_for``'s
own "one builder, two callers" shape.

Execution order is fixed (AD-1): severity check via ``principal_for`` FIRST,
then the action-policy gate (``authz.action_policy.decide``, Story 4.4,
AD-27), then the deterministic handler from ``CommandHandlerRegistry`` — no
model call, ever. No generic dispatch: the command type names the
``CommandSpec`` and the handler, both resolved from closed, explicit dicts.

PURPOSELY DOES NO DATABASE OR JOURNAL I/O. AD-7 restricts this package's
imports to ``authz/`` + ``pipeline/durable`` (+ stdlib/pydantic) — never
``journal`` directly. Recording ``command.enqueued``/``command.completed`` is
therefore ``pipeline/durable/store.py``'s job (its ``create``/``mark_delivered``
already own the ``tasks`` row's lifecycle bookkeeping); this function only
decides WHETHER a command may run and WHAT running it produced, and returns
that decision to a caller that already holds a store.
"""

from __future__ import annotations

import json

from stackowl.authz.action_policy import decide
from stackowl.authz.requester import principal_for
from stackowl.commands.spec.context import CommandContext, CommandOutcome
from stackowl.commands.spec.errors import CommandNeedsDecisionError, CommandRefusedError
from stackowl.commands.spec.handlers import CommandHandlerRegistry
from stackowl.commands.spec.registry import CommandSpecRegistry

# AD-7 — see registry.py's identical import for why `log` is allowed here.
from stackowl.infra.observability import log
from stackowl.pipeline.durable.task import DurableTask

#: `CommandNeedsDecisionError.payload_summary`'s bound -- Code Map: "computed
#: via json.dumps(payload.model_dump(), default=str)[:256], stdlib only".
_PAYLOAD_SUMMARY_MAX_LEN = 256


async def execute_command_task(task: DurableTask) -> CommandOutcome:
    """Run *task* (a claimed ``kind='command'`` row) through the one door.

    Never itself updates ``task``'s row status or records a journal event —
    the caller (the ``TaskLoop`` dispatch loop for the tick path,
    ``submit_command`` for the inline path) owns that via the store, exactly
    as ``task_loop_runner.py``'s existing goal-task runner never calls
    ``store.mark_delivered``/``fail_and_requeue`` itself either.

    Raises :class:`~stackowl.commands.spec.errors.CommandTypeNotDeclaredError`
    when ``task.command_type`` names nothing in :class:`CommandSpecRegistry`/
    :class:`CommandHandlerRegistry`, :class:`~stackowl.commands.spec.
    errors.CommandRefusedError` when the severity check refuses (see this
    module's docstring: not exercised by any live surface today, since
    ``principal_for`` currently grants every severity), and
    :class:`~stackowl.commands.spec.errors.CommandNeedsDecisionError` when
    ``authz.action_policy.decide`` (Story 4.4, AD-27) decides this command
    may not run at once — the caller parks it and opens a Needs-you item
    rather than treating that as a failure.
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
    # 2b. DECISION — the action-policy gate (Story 4.4, AD-27), run strictly
    # AFTER the severity check and BEFORE the handler (AD-1's fixed order:
    # "severity check -> action-policy gate -> consent -> handler"). A
    # RESUMED row (`gate_verdict == "approved"`, set by `store.
    # resume_command_after_answer` once its Needs-you item was answered)
    # skips straight to the handler — the decision already happened once,
    # and re-deciding would open a second Needs-you item for a command_id
    # whose first one is already resolved and free.
    #
    # Story 4.6 — `decide()`'s `authority_grant_id` input is never passed a
    # real value here: no live caller resolves a `standing_authority` row
    # before this call yet (spec-4-6 Boundaries — the whole mechanism is
    # proven by direct unit tests against `decide()` itself). `gate.
    # authority_grant_id` is therefore always `None` on this path today, and
    # `context.authority_grant_id` below carries that same `None` — declared
    # and threaded, not yet load-bearing, the same shape `nonce`/
    # `utterance_id` shipped in Story 4.3.
    authority_grant_id: str | None = None
    if task.gate_verdict != "approved":
        gate = decide(
            severity=spec.severity, reversible=spec.reversible,
            requester_kind=requester_kind,  # type: ignore[arg-type]
        )
        if gate.outcome != "run_at_once":
            payload_summary = json.dumps(
                payload.model_dump(), default=str,
            )[:_PAYLOAD_SUMMARY_MAX_LEN]
            log.tasks.info(
                "[commands] execute.execute_command_task: the gate decided "
                "this command needs a decision before it may run",
                extra={"_fields": {
                    "command_type": command_type, "outcome": gate.outcome,
                    "requester_kind": requester_kind,
                }},
            )
            raise CommandNeedsDecisionError(command_type, gate.outcome, payload_summary)
        authority_grant_id = gate.authority_grant_id
    context = CommandContext(
        command_id=command_id, command_type=command_type,
        requester_kind=requester_kind, utterance_id=task.utterance_id,
        authority_grant_id=authority_grant_id,
    )
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
