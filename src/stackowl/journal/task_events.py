"""Attrs models for the four durable-task lifecycle events, and their
registration (Story 2.1).

One emitting process for all four: ``pipeline.durable`` -- the durable task
loop is the only place these fire today, at the exact call site the state
change commits (AD-24). Importing this module registers all four types as a
side effect; ``journal/__init__.py`` imports it for exactly that reason so any
importer of ``journal`` gets a working registry with no separate step to
remember.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from stackowl.journal.enums import AttentionClass, RecordKind
from stackowl.journal.models import JournalAttrsBase
from stackowl.journal.registry import EventTypeSpec, get_registry

_EMITTING_PROCESS = "pipeline.durable"

#: AD-4, verbatim: "attrs hold ids, numbers, closed enums and bounded labels
#: of at most 64 characters" -- the one bound every open string field below
#: enforces, rather than each field re-typing its own limit.
_MAX_LABEL_LEN = 64


class TaskEnqueuedAttrs(JournalAttrsBase):
    """``task.enqueued`` -- a row the loop may pick up was written."""

    #: chat / schedule / subgoal / incident, or None on a legacy-shaped call.
    trigger_kind: str | None = Field(default=None, max_length=_MAX_LABEL_LEN)
    depends_on_count: int = 0
    max_attempts: int


class TaskClaimedAttrs(JournalAttrsBase):
    """``task.claimed`` -- one worker won the compare-and-set claim."""

    lease_owner: str = Field(max_length=_MAX_LABEL_LEN)
    lease_seconds: int


class TaskFinishedAttrs(JournalAttrsBase):
    """``task.finished`` -- the loop reached a successful terminal state.

    ``completion_mode`` carries the delivered-vs-unaddressed distinction; the
    envelope's ``outcome`` stays ``ok`` for both, since the conventions'
    outcome enum has no value for "nobody was waiting" (Design Notes).
    """

    completion_mode: Literal["delivered", "unaddressed"]


class TaskDeadLetteredAttrs(JournalAttrsBase):
    """``task.dead_lettered`` -- the loop stopped retrying for good.

    Fires from ``fail_and_requeue``'s permanent/ceiling branch AND from
    ``_deps_satisfied``'s dependency-failure cascade, which is the only path
    that populates ``dependency_ids`` (the failed dependencies that took this
    row down with it -- comma-joined, matching ``depends_on``'s and
    ``banned_capabilities``'s convention elsewhere in this store, since more
    than one dependency can fail before the cascade is next evaluated).
    """

    #: chat / schedule / subgoal / incident -- what kind of task gave up.
    task_kind: str | None = Field(default=None, max_length=_MAX_LABEL_LEN)
    attempt_count: int
    max_attempts: int
    lease_owner: str | None = Field(default=None, max_length=_MAX_LABEL_LEN)
    failure_class: str | None = Field(default=None, max_length=_MAX_LABEL_LEN)
    permanent: bool
    dependency_ids: str | None = None


def _register() -> None:
    registry = get_registry()
    registry.register(EventTypeSpec(
        type="task.enqueued", schema_version=1, attrs_model=TaskEnqueuedAttrs,
        emitting_process=_EMITTING_PROCESS, record_kind=RecordKind.TASK,
        attention_class=AttentionClass.AMBIENT,
    ))
    registry.register(EventTypeSpec(
        type="task.claimed", schema_version=1, attrs_model=TaskClaimedAttrs,
        emitting_process=_EMITTING_PROCESS, record_kind=RecordKind.TASK,
        attention_class=AttentionClass.AMBIENT,
    ))
    registry.register(EventTypeSpec(
        type="task.finished", schema_version=1, attrs_model=TaskFinishedAttrs,
        emitting_process=_EMITTING_PROCESS, record_kind=RecordKind.TASK,
        attention_class=AttentionClass.AMBIENT,
    ))
    registry.register(EventTypeSpec(
        type="task.dead_lettered", schema_version=1, attrs_model=TaskDeadLetteredAttrs,
        emitting_process=_EMITTING_PROCESS, record_kind=RecordKind.TASK,
        # AD-5, verbatim: task.dead_lettered is one of the two NAMED
        # needs_you/high examples -- the loop gave up, and that is exactly
        # what must reach the owner rather than dissolve into a log file.
        attention_class=AttentionClass.NEEDS_YOU,
    ))


_register()
