"""Attrs models for the scheduler job-lifecycle events, and their
registration (Story 2.6; ``job.paused``/``job.resumed`` added by Story 4.3).

One emitting process for all six: ``scheduler`` -- ``scheduler.py`` and
``scheduler_mutations.py`` are the only two places a job's own lifecycle
transitions, and both record at the exact call site the state change commits
(AD-24), mirroring ``task_events.py``'s shape for the durable task loop.
Importing this module registers all six types as a side effect;
``journal/__init__.py`` imports it for exactly that reason.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from pydantic import BaseModel, ConfigDict, Field

from stackowl.journal.enums import AttentionClass, Intensity, NeedsYouKind, RecordKind
from stackowl.journal.models import JournalAttrsBase
from stackowl.journal.records import (
    ExpiredRecord,
    get_record_reader_registry,
    read_sqlite_record,
    refuse_unless_owner,
)
from stackowl.journal.registry import EventTypeSpec, get_registry

if TYPE_CHECKING:  # pragma: no cover -- typing-only
    from stackowl.db.pool import DbPool

_EMITTING_PROCESS = "scheduler"
_TABLE = "jobs"

#: AD-4, verbatim: "attrs hold ids, numbers, closed enums and bounded labels
#: of at most 64 characters" -- the one bound every open string field below
#: enforces, rather than each field re-typing its own limit. Mirrors
#: ``task_events.py``'s own ``_MAX_LABEL_LEN``.
_MAX_LABEL_LEN = 64


class JobStartedAttrs(JournalAttrsBase):
    """``job.started`` -- a scheduler dispatcher won the pending->running CAS
    claim for this job's row (the poller's ``_run_job`` or an out-of-band
    ``run_now``)."""

    handler_name: str = Field(max_length=_MAX_LABEL_LEN)


class JobFinishedAttrs(JournalAttrsBase):
    """``job.finished`` -- a run completed successfully. Fires for both a
    recurring job's re-arm-to-next-slot and a one-shot's retirement (row
    delete) -- the same job it started, its outcome now recorded."""

    handler_name: str = Field(max_length=_MAX_LABEL_LEN)


class JobFailedAttrs(JournalAttrsBase):
    """``job.failed`` -- a run failed but the job LIVES ON: a mid-run retry
    still within budget (``_settle``'s retry branch), a one-shot re-armed onto
    the backoff ladder, or a recurring job re-armed onto its next cadence
    slot. Never fires for the retry-exhausted give-up -- that is
    ``job.parked`` (AD-5's named needs_you/high example)."""

    handler_name: str = Field(max_length=_MAX_LABEL_LEN)
    failure_count: int


class JobParkedAttrs(JournalAttrsBase):
    """``job.parked`` -- a ONE-SHOT job exhausted its retries and will never
    run again (F-60: a recurring job never parks -- no circuit breaker, owner
    decision). The loop's give-up, exactly what AD-5 says must reach the
    owner."""

    handler_name: str = Field(max_length=_MAX_LABEL_LEN)
    attempt_count: int


class JobPausedAttrs(JournalAttrsBase):
    """``job.paused`` -- Story 4.3: ``JobScheduler.pause`` committed, driven
    through a COMMAND task (``scheduling.pause_job``). Did not exist before
    this story -- ``ActorKind``'s own docstring named ``CommandContext`` as
    the reason: pause/resume had no actor/requester-kind carrier to record
    against until this one did. ``command_id`` is ``None`` only for a legacy
    direct call (``context=None`` -- every caller before this story, none of
    which produced a COMMAND row to attribute this event to)."""

    command_id: str | None = Field(default=None, max_length=_MAX_LABEL_LEN)


class JobResumedAttrs(JournalAttrsBase):
    """``job.resumed`` -- Story 4.3: ``JobScheduler.resume`` committed,
    driven through a COMMAND task (``scheduling.resume_job``). See
    :class:`JobPausedAttrs` for why this did not exist before this story."""

    command_id: str | None = Field(default=None, max_length=_MAX_LABEL_LEN)


def _narrate_started(attrs: JournalAttrsBase, name: str) -> str:
    return f"Job {name} started."


def _narrate_finished(attrs: JournalAttrsBase, name: str) -> str:
    return f"Job {name} finished."


def _narrate_failed(attrs: JournalAttrsBase, name: str) -> str:
    return f"Job {name} failed and will be retried."


def _narrate_parked(attrs: JournalAttrsBase, name: str) -> str:
    from typing import cast

    parked = cast(JobParkedAttrs, attrs)
    return f"Job {name} gave up after {parked.attempt_count} attempts."


def _narrate_paused(attrs: JournalAttrsBase, name: str) -> str:
    return f"Job {name} paused."


def _narrate_resumed(attrs: JournalAttrsBase, name: str) -> str:
    return f"Job {name} resumed."


def _register() -> None:
    registry = get_registry()
    registry.register(EventTypeSpec(
        type="job.started", schema_version=1, attrs_model=JobStartedAttrs,
        emitting_process=_EMITTING_PROCESS, record_kind=RecordKind.JOB,
        attention_class=AttentionClass.AMBIENT, intensity=None,
        table=_TABLE, narrate=_narrate_started,
    ))
    registry.register(EventTypeSpec(
        type="job.finished", schema_version=1, attrs_model=JobFinishedAttrs,
        emitting_process=_EMITTING_PROCESS, record_kind=RecordKind.JOB,
        attention_class=AttentionClass.AMBIENT, intensity=None,
        table=_TABLE, narrate=_narrate_finished,
    ))
    registry.register(EventTypeSpec(
        type="job.failed", schema_version=1, attrs_model=JobFailedAttrs,
        emitting_process=_EMITTING_PROCESS, record_kind=RecordKind.JOB,
        attention_class=AttentionClass.AMBIENT, intensity=None,
        table=_TABLE, narrate=_narrate_failed,
    ))
    registry.register(EventTypeSpec(
        type="job.parked", schema_version=1, attrs_model=JobParkedAttrs,
        emitting_process=_EMITTING_PROCESS, record_kind=RecordKind.JOB,
        # AD-5, verbatim: job.parked is one of the two NAMED needs_you/high
        # examples -- the scheduler gave up on this one-shot for good.
        attention_class=AttentionClass.NEEDS_YOU, intensity=Intensity.HIGH,
        # Story 3.1 (AD-28) -- a give-up opens a durable `incident` item.
        needs_you_kind=NeedsYouKind.INCIDENT,
        table=_TABLE, narrate=_narrate_parked,
    ))
    registry.register(EventTypeSpec(
        type="job.paused", schema_version=1, attrs_model=JobPausedAttrs,
        emitting_process=_EMITTING_PROCESS, record_kind=RecordKind.JOB,
        attention_class=AttentionClass.AMBIENT, intensity=None,
        table=_TABLE, narrate=_narrate_paused,
    ))
    registry.register(EventTypeSpec(
        type="job.resumed", schema_version=1, attrs_model=JobResumedAttrs,
        emitting_process=_EMITTING_PROCESS, record_kind=RecordKind.JOB,
        attention_class=AttentionClass.AMBIENT, intensity=None,
        table=_TABLE, narrate=_narrate_resumed,
    ))


_register()


class JobRecordView(BaseModel):
    """Typed view of one ``jobs`` row -- the registered reader's return shape
    for ``RecordKind.JOB``/``sqlite`` (AD-4)."""

    model_config = ConfigDict(frozen=True)

    job_id: str
    handler_name: str
    schedule: str
    idempotency_key: str
    last_run_at: str | None = None
    next_run_at: str
    status: str
    retry_count: int
    created_at: str


async def read_job_record(
    db_pool: DbPool | None, locator: dict[str, str], *, owner_id: str,
) -> JobRecordView | ExpiredRecord:
    """The registered reader for ``RecordKind.JOB``/``sqlite`` (AD-4): opens
    the ``jobs`` row a ``job.*`` event's ``record_ref`` points at."""
    refuse_unless_owner(RecordKind.JOB, owner_id)
    row = await read_sqlite_record(
        db_pool, table=_TABLE, id_column="job_id",
        id_value=locator.get("job_id", ""), view_model=JobRecordView,
    )
    if row is None:
        return ExpiredRecord(
            record_kind=RecordKind.JOB, locator=locator,
            reason="the jobs row this event referenced is gone",
        )
    return cast(JobRecordView, row)


get_record_reader_registry().register(RecordKind.JOB, "sqlite", read_job_record)
