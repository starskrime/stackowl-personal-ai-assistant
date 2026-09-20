"""``scheduling.*`` ``CommandSpec``s -- the pilot pause/resume pair (Story
4.3) plus the 7 command types Story 4.7 adds for the rest of scheduling:
``create_job``/``edit_job``/``delete_job``/``run_now_job`` (``cronjob``'s
remaining actions), ``set_owl_schedule`` (``owl_schedule``'s snooze),
``pause_owl_job``/``resume_owl_job`` (``owl_schedule``'s pause/resume,
declared DISTINCTLY from the pilot pair per the Story 4.2 census's own
authored split -- both pairs call the identical ``JobScheduler.pause``/
``.resume``, just from a different command type so ``cronjob``'s and
``owl_schedule``'s undo/audit trails never merge).

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
CREATE_JOB = "scheduling.create_job"
EDIT_JOB = "scheduling.edit_job"
DELETE_JOB = "scheduling.delete_job"
RUN_NOW_JOB = "scheduling.run_now_job"
SET_OWL_SCHEDULE = "scheduling.set_owl_schedule"
PAUSE_OWL_JOB = "scheduling.pause_owl_job"
RESUME_OWL_JOB = "scheduling.resume_owl_job"


class JobLifecyclePayload(BaseModel):
    """The one payload shape every job_id-only command shares: the pilot
    pause/resume pair, plus (Story 4.7) delete_job/run_now_job/
    pause_owl_job/resume_owl_job."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    job_id: str = Field(min_length=1)


#: AD-4's bound, mirrors ``journal/job_events.py``'s own module-level constant.
_MAX_LABEL_LEN = 64


class CreateJobPayload(BaseModel):
    """``scheduling.create_job``'s payload -- everything ``cronjob``'s
    ``_create``/``_watch`` pass to :meth:`JobScheduler.create_job` today,
    minus ``idempotency_key``/``preauthorized_command_types`` (neither live
    caller sets either — no tool passes them, so they stay the method's own
    defaults rather than growing an unused field here)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    handler_name: str = Field(min_length=1, max_length=_MAX_LABEL_LEN)
    schedule: str = Field(min_length=1)
    params: dict[str, object] = Field(default_factory=dict)
    replay_missed: bool = False
    primary_channel: str | None = None
    target_channels: list[str] = Field(default_factory=list)
    target_addresses: dict[str, str | int] = Field(default_factory=dict)


class EditJobPayload(BaseModel):
    """``scheduling.edit_job``'s payload -- mirrors ``JobScheduler.update_job``'s
    own optional ``schedule``/``goal`` (``params`` is not exposed here: no
    live caller edits raw params through this command, only via ``goal``)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    job_id: str = Field(min_length=1)
    schedule: str | None = None
    goal: str | None = None


class SnoozeJobPayload(BaseModel):
    """``scheduling.set_owl_schedule``'s payload -- ``owl_schedule``'s snooze
    action. ``until`` is an ISO-8601 UTC instant (mirrors
    :meth:`JobScheduler.snooze`'s own ``until_iso`` parameter)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    job_id: str = Field(min_length=1)
    until: str = Field(min_length=1)


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


async def _pause_owl_job_handler(
    payload: JobLifecyclePayload, context: CommandContext,
) -> CommandOutcome:
    """Calls the IDENTICAL ``JobScheduler.pause`` the pilot pair's own
    ``_pause_job_handler`` calls -- only ``context.command_type`` differs
    (``scheduling.pause_owl_job`` vs ``scheduling.pause_job``), which is what
    keeps ``cronjob``'s and ``owl_schedule``'s undo/audit trails separate."""
    from stackowl.pipeline.services import get_services

    db = get_services().db_pool
    if db is None:
        log.scheduler.warning(
            "[commands] scheduler.commands._pause_owl_job_handler: no db pool",
            extra={"_fields": {"job_id": payload.job_id}},
        )
        return CommandOutcome(
            success=False, error="scheduling unavailable (no database configured)",
        )
    scheduler = JobScheduler(db=db)
    await scheduler.pause(payload.job_id, context=context)
    return CommandOutcome(success=True, result={"job_id": payload.job_id})


async def _resume_owl_job_handler(
    payload: JobLifecyclePayload, context: CommandContext,
) -> CommandOutcome:
    """See :func:`_pause_owl_job_handler` -- calls the same ``JobScheduler.resume``
    ``_resume_job_handler`` calls, under the ``owl_schedule``-owned command type."""
    from stackowl.pipeline.services import get_services

    db = get_services().db_pool
    if db is None:
        log.scheduler.warning(
            "[commands] scheduler.commands._resume_owl_job_handler: no db pool",
            extra={"_fields": {"job_id": payload.job_id}},
        )
        return CommandOutcome(
            success=False, error="scheduling unavailable (no database configured)",
        )
    scheduler = JobScheduler(db=db)
    await scheduler.resume(payload.job_id, context=context)
    return CommandOutcome(success=True, result={"job_id": payload.job_id})


async def _create_job_handler(
    payload: CreateJobPayload, context: CommandContext,
) -> CommandOutcome:
    from stackowl.pipeline.services import get_services

    db = get_services().db_pool
    if db is None:
        log.scheduler.warning(
            "[commands] scheduler.commands._create_job_handler: no db pool",
            extra={"_fields": {"handler_name": payload.handler_name}},
        )
        return CommandOutcome(
            success=False, error="scheduling unavailable (no database configured)",
        )
    scheduler = JobScheduler(db=db)
    job = await scheduler.create_job(
        handler_name=payload.handler_name,
        schedule=payload.schedule,
        params=dict(payload.params),
        replay_missed=payload.replay_missed,
        primary_channel=payload.primary_channel,
        target_channels=list(payload.target_channels),
        target_addresses=dict(payload.target_addresses),
        context=context,
    )
    # The whole Job, JSON-safe (pydantic model_dump) — the caller (cronjob's
    # _create/_watch) reconstructs a Job from this to build its own
    # job_summary()-shaped tool payload, exactly as it would from a direct
    # scheduler.create_job() return before this story.
    return CommandOutcome(success=True, result=job.model_dump(mode="json"))


async def _edit_job_handler(
    payload: EditJobPayload, context: CommandContext,
) -> CommandOutcome:
    from stackowl.pipeline.services import get_services

    db = get_services().db_pool
    if db is None:
        log.scheduler.warning(
            "[commands] scheduler.commands._edit_job_handler: no db pool",
            extra={"_fields": {"job_id": payload.job_id}},
        )
        return CommandOutcome(
            success=False, error="scheduling unavailable (no database configured)",
        )
    scheduler = JobScheduler(db=db)
    updated = await scheduler.update_job(
        payload.job_id, schedule=payload.schedule, goal=payload.goal, context=context,
    )
    if updated is None:
        return CommandOutcome(success=False, error=f"no such job: {payload.job_id!r}")
    return CommandOutcome(success=True, result=updated.model_dump(mode="json"))


async def _delete_job_handler(
    payload: JobLifecyclePayload, context: CommandContext,
) -> CommandOutcome:
    from stackowl.pipeline.services import get_services

    db = get_services().db_pool
    if db is None:
        log.scheduler.warning(
            "[commands] scheduler.commands._delete_job_handler: no db pool",
            extra={"_fields": {"job_id": payload.job_id}},
        )
        return CommandOutcome(
            success=False, error="scheduling unavailable (no database configured)",
        )
    scheduler = JobScheduler(db=db)
    await scheduler.stop_job(payload.job_id, context=context)
    return CommandOutcome(success=True, result={"job_id": payload.job_id})


async def _run_now_job_handler(
    payload: JobLifecyclePayload, context: CommandContext,
) -> CommandOutcome:
    from stackowl.pipeline.services import get_services

    db = get_services().db_pool
    if db is None:
        log.scheduler.warning(
            "[commands] scheduler.commands._run_now_job_handler: no db pool",
            extra={"_fields": {"job_id": payload.job_id}},
        )
        return CommandOutcome(
            success=False, error="scheduling unavailable (no database configured)",
        )
    scheduler = JobScheduler(db=db)
    result = await scheduler.run_now(payload.job_id, context=context)
    if result is None:
        return CommandOutcome(success=False, error=f"no such job: {payload.job_id!r}")
    # Mirrors cronjob._run's own pre-4.7 shape exactly: the COMMAND itself
    # succeeded at TRIGGERING the run even when the job's own handler did
    # not — a benign "already running via its own schedule" race is
    # surfaced as a note, never a command failure (the same reasoning
    # cronjob._run's own comment states for why this must not read as a
    # capability failure to downstream honesty judges).
    if not result.success and result.error and "not runnable now" in result.error:
        return CommandOutcome(
            success=True,
            result={
                "ran": False, "job_id": payload.job_id,
                "note": "already running via its own schedule — no manual trigger needed",
            },
        )
    return CommandOutcome(
        success=True,
        result={
            "ran": True, "job_id": payload.job_id, "success": result.success,
            "output": result.output, "error": result.error,
        },
    )


async def _set_owl_schedule_handler(
    payload: SnoozeJobPayload, context: CommandContext,
) -> CommandOutcome:
    from stackowl.pipeline.services import get_services

    db = get_services().db_pool
    if db is None:
        log.scheduler.warning(
            "[commands] scheduler.commands._set_owl_schedule_handler: no db pool",
            extra={"_fields": {"job_id": payload.job_id}},
        )
        return CommandOutcome(
            success=False, error="scheduling unavailable (no database configured)",
        )
    scheduler = JobScheduler(db=db)
    await scheduler.snooze(payload.job_id, payload.until, context=context)
    return CommandOutcome(
        success=True, result={"job_id": payload.job_id, "until": payload.until},
    )


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

    # ---------------------------------------------------------- Story 4.7
    pause_owl_spec = CommandSpec(
        command_type=PAUSE_OWL_JOB,
        payload_model=JobLifecyclePayload,
        severity="write",
        reversible=True,
        undo_command_type=RESUME_OWL_JOB,
    )
    resume_owl_spec = CommandSpec(
        command_type=RESUME_OWL_JOB,
        payload_model=JobLifecyclePayload,
        severity="write",
        reversible=True,
        undo_command_type=PAUSE_OWL_JOB,
    )
    create_spec = CommandSpec(
        command_type=CREATE_JOB,
        payload_model=CreateJobPayload,
        severity="write",
        # Reversible: a freshly created job's own undo is deleting it.
        reversible=True,
        undo_command_type=DELETE_JOB,
    )
    edit_spec = CommandSpec(
        command_type=EDIT_JOB,
        payload_model=EditJobPayload,
        severity="write",
        # Reversible → itself: undo re-submits edit_job with the CAPTURED
        # prior {job_id, schedule, goal} (commands/spec/undo.py's
        # undo_payload path), restoring the values this edit overwrote.
        reversible=True,
        undo_command_type=EDIT_JOB,
    )
    delete_spec = CommandSpec(
        command_type=DELETE_JOB,
        payload_model=JobLifecyclePayload,
        severity="write",
        # Irreversible: deleting a job destroys everything an undo would
        # need to reconstruct it (Design Notes: this forces step-up for
        # EVERY requester kind, including the owner — the gate's honest,
        # unmodified behavior for a genuinely one-way action).
        reversible=False,
    )
    run_now_spec = CommandSpec(
        command_type=RUN_NOW_JOB,
        payload_model=JobLifecyclePayload,
        severity="write",
        # Irreversible: running a job out of band cannot be undone (its
        # handler's own side effects already happened).
        reversible=False,
    )
    set_owl_schedule_spec = CommandSpec(
        command_type=SET_OWL_SCHEDULE,
        payload_model=SnoozeJobPayload,
        severity="write",
        # Reversible → itself: undo re-submits set_owl_schedule with the
        # CAPTURED prior {job_id, until: <prior next_run_at>}, restoring the
        # schedule this snooze displaced.
        reversible=True,
        undo_command_type=SET_OWL_SCHEDULE,
    )
    CommandSpecRegistry.register(pause_owl_spec)
    CommandSpecRegistry.register(resume_owl_spec)
    CommandSpecRegistry.register(create_spec)
    CommandSpecRegistry.register(edit_spec)
    CommandSpecRegistry.register(delete_spec)
    CommandSpecRegistry.register(run_now_spec)
    CommandSpecRegistry.register(set_owl_schedule_spec)
    CommandHandlerRegistry.register(PAUSE_OWL_JOB, _pause_owl_job_handler)  # type: ignore[arg-type]
    CommandHandlerRegistry.register(RESUME_OWL_JOB, _resume_owl_job_handler)  # type: ignore[arg-type]
    CommandHandlerRegistry.register(CREATE_JOB, _create_job_handler)  # type: ignore[arg-type]
    CommandHandlerRegistry.register(EDIT_JOB, _edit_job_handler)  # type: ignore[arg-type]
    CommandHandlerRegistry.register(DELETE_JOB, _delete_job_handler)  # type: ignore[arg-type]
    CommandHandlerRegistry.register(RUN_NOW_JOB, _run_now_job_handler)  # type: ignore[arg-type]
    CommandHandlerRegistry.register(SET_OWL_SCHEDULE, _set_owl_schedule_handler)  # type: ignore[arg-type]

    log.scheduler.info(
        "[commands] scheduler.commands: CommandSpecs registered",
        extra={"_fields": {"command_types": [
            PAUSE_JOB, RESUME_JOB, PAUSE_OWL_JOB, RESUME_OWL_JOB,
            CREATE_JOB, EDIT_JOB, DELETE_JOB, RUN_NOW_JOB, SET_OWL_SCHEDULE,
        ]}},
    )


_register()
