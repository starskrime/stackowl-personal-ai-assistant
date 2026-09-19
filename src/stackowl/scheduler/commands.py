"""The two pilot ``CommandSpec``s -- ``scheduling.pause_job``/``resume_job``
(Story 4.3, the "one door" pilot migration).

PLACEMENT: here, not ``commands/spec/`` -- AD-7 restricts what
``commands/spec/`` may import (``authz/`` + ``pipeline/durable`` only,
tripwire-enforced); a SUBSYSTEM declaring its own commands is not restricted
the other way, and this module freely imports both ``commands.spec`` (to
register into) and ``scheduler.scheduler`` (the mutator it wraps) — the exact
combination ``commands/spec/`` itself may never hold. Every future migrated
subsystem (4.7-4.10) gets its own ``<subsystem>/commands.py`` following this
same shape; ``commands/spec/`` never grows subsystem-specific knowledge.

Importing this module registers both ``CommandSpec``s and their handlers as a
side effect (mirrors ``journal/task_events.py``'s own "importing this module
registers ... as a side effect" shape) — imported once from
``startup/orchestrator.py``, beside ``register_all_commands``.

Handlers resolve their own db pool via ``get_services()`` rather than taking
one as a parameter: ``CommandHandler``'s shape (``command_spec.py``) is fixed
at ``(payload, context) -> CommandOutcome`` for every subsystem, so a handler
that needed a bespoke extra argument would break the closed dispatch the
``CommandHandlerRegistry`` runs through.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from stackowl.commands.spec.command_spec import CommandSpec
from stackowl.commands.spec.context import CommandContext, CommandOutcome
from stackowl.commands.spec.handlers import CommandHandlerRegistry
from stackowl.commands.spec.registry import CommandSpecRegistry
from stackowl.infra.observability import log
from stackowl.scheduler.scheduler import JobScheduler

PAUSE_JOB = "scheduling.pause_job"
RESUME_JOB = "scheduling.resume_job"


class JobLifecyclePayload(BaseModel):
    """The one payload shape both pilot commands share."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    job_id: str = Field(min_length=1)


async def _pause_job_handler(
    payload: JobLifecyclePayload, context: CommandContext,
) -> CommandOutcome:
    from stackowl.pipeline.services import get_services

    db = get_services().db_pool
    if db is None:
        log.scheduler.warning(
            "[commands] scheduler.commands._pause_job_handler: no db pool",
            extra={"_fields": {"job_id": payload.job_id}},
        )
        return CommandOutcome(
            success=False, error="scheduling unavailable (no database configured)",
        )
    scheduler = JobScheduler(db=db)
    await scheduler.pause(payload.job_id, context=context)
    return CommandOutcome(success=True, result={"job_id": payload.job_id})


async def _resume_job_handler(
    payload: JobLifecyclePayload, context: CommandContext,
) -> CommandOutcome:
    from stackowl.pipeline.services import get_services

    db = get_services().db_pool
    if db is None:
        log.scheduler.warning(
            "[commands] scheduler.commands._resume_job_handler: no db pool",
            extra={"_fields": {"job_id": payload.job_id}},
        )
        return CommandOutcome(
            success=False, error="scheduling unavailable (no database configured)",
        )
    scheduler = JobScheduler(db=db)
    await scheduler.resume(payload.job_id, context=context)
    return CommandOutcome(success=True, result={"job_id": payload.job_id})


def _register() -> None:
    pause_spec = CommandSpec(
        command_type=PAUSE_JOB,
        payload_model=JobLifecyclePayload,
        severity="write",
        # Reversible: resuming undoes a pause and vice versa — declared here
        # (Story 4.3), never invoked (Story 4.5's undo execution is out of
        # this story's scope).
        reversible=True,
        undo_command_type=RESUME_JOB,
    )
    resume_spec = CommandSpec(
        command_type=RESUME_JOB,
        payload_model=JobLifecyclePayload,
        severity="write",
        reversible=True,
        undo_command_type=PAUSE_JOB,
    )
    CommandSpecRegistry.register(pause_spec)
    CommandSpecRegistry.register(resume_spec)
    CommandHandlerRegistry.register(PAUSE_JOB, _pause_job_handler)  # type: ignore[arg-type]
    CommandHandlerRegistry.register(RESUME_JOB, _resume_job_handler)  # type: ignore[arg-type]
    log.scheduler.info(
        "[commands] scheduler.commands: pilot CommandSpecs registered",
        extra={"_fields": {"command_types": [PAUSE_JOB, RESUME_JOB]}},
    )


_register()
