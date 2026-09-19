"""Attrs models for the three COMMAND-task lifecycle events, and their
registration (Story 4.3, AD-26).

One emitting process: ``pipeline.durable`` -- the same durable task loop
``task_events.py`` already emits from, since a COMMAND row IS a ``tasks`` row
(migration 0151's ``kind='command'``), not a second table.
``pipeline/durable/store.py`` is the ONE writer of all three:
``command.enqueued`` inside :meth:`~stackowl.pipeline.durable.store.
DurableTaskStore.create` (its ``kind='command'`` branch), ``command.completed``
inside :meth:`~stackowl.pipeline.durable.store.DurableTaskStore.mark_delivered`
(its ``kind='command'`` branch), ``command.failed`` inside
:meth:`~stackowl.pipeline.durable.store.DurableTaskStore.record_command_failed`
(called from ``commands/spec/submit.py``'s inline-execution failure branches)
-- deliberately NOT emitted from ``commands/spec/`` itself, which stays inside
AD-7's import boundary (``authz/`` + ``pipeline/durable`` only) by never
importing ``journal`` directly. All three are the COMMAND TASK WRAPPER's own
lifecycle bookkeeping, never the domain event a mutator's own commit records
(e.g. ``job.paused``) -- AD-26: "the COMMAND handler itself records only
``command.*`` lifecycle events, never the domain event (no double-recording)."
Importing this module registers all three as a side effect; ``journal/
__init__.py`` imports it for exactly that reason.

Reuses ``RecordKind.TASK`` and ``table="tasks"`` rather than declaring a new
``RecordKind`` -- AD-2's own eventual-domain list has no separate "commands"
entry; a COMMAND row is a kind of task, read back by the SAME
``read_task_record`` reader ``task_events.py`` already registers for
``RecordKind.TASK``/``sqlite``.
"""

from __future__ import annotations

from pydantic import Field

from stackowl.journal.enums import AttentionClass, RecordKind
from stackowl.journal.models import JournalAttrsBase
from stackowl.journal.registry import EventTypeSpec, get_registry

_EMITTING_PROCESS = "pipeline.durable"
_TABLE = "tasks"

#: AD-4, verbatim: "attrs hold ids, numbers, closed enums and bounded labels
#: of at most 64 characters" -- mirrors ``task_events.py``'s own bound.
_MAX_LABEL_LEN = 64


class CommandEnqueuedAttrs(JournalAttrsBase):
    """``command.enqueued`` -- ``submit_command`` wrote a claimable
    ``kind='command'`` row."""

    command_type: str = Field(max_length=_MAX_LABEL_LEN)
    requester_kind: str = Field(max_length=_MAX_LABEL_LEN)


class CommandCompletedAttrs(JournalAttrsBase):
    """``command.completed`` -- the deterministic handler ran and returned a
    successful outcome, recorded by :meth:`~stackowl.pipeline.durable.store.
    DurableTaskStore.mark_delivered` (the SAME store call both the inline,
    non-tick ``submit_command`` path and the tick-driven ``TaskLoop`` use to
    land a COMMAND row) -- one mechanism, so "inline" and "tick" completion
    never risk recording this differently."""

    command_type: str = Field(max_length=_MAX_LABEL_LEN)


class CommandFailedAttrs(JournalAttrsBase):
    """``command.failed`` -- ``submit_command``'s inline execution attempt did
    NOT succeed: either the severity check refused it
    (``failure_class="command_refused"``) or the deterministic handler ran
    and reported its own failure (``failure_class="command_handler_failed"``).
    ``failure_class`` is a closed, bounded label (AD-4: never exception/free
    text) -- mirrors ``job_events.py``'s own ``JobFailedAttrs`` shape for the
    scheduler's equivalent event. Recorded only for the ONE inline attempt
    ``submit_command`` makes; a subsequent tick-driven retry of the SAME row
    (requeued via ``fail_and_requeue``) is covered by the existing generic
    ``task.*`` lifecycle, not a second ``command.failed`` row per attempt."""

    command_type: str = Field(max_length=_MAX_LABEL_LEN)
    failure_class: str = Field(max_length=_MAX_LABEL_LEN)


def _narrate_enqueued(attrs: JournalAttrsBase, name: str) -> str:
    return f"Command {name} was queued."


def _narrate_completed(attrs: JournalAttrsBase, name: str) -> str:
    return f"Command {name} completed."


def _narrate_failed(attrs: JournalAttrsBase, name: str) -> str:
    return f"Command {name} failed."


def _register() -> None:
    registry = get_registry()
    registry.register(EventTypeSpec(
        type="command.enqueued", schema_version=1, attrs_model=CommandEnqueuedAttrs,
        emitting_process=_EMITTING_PROCESS, record_kind=RecordKind.TASK,
        attention_class=AttentionClass.AMBIENT, intensity=None,
        table=_TABLE, narrate=_narrate_enqueued,
    ))
    registry.register(EventTypeSpec(
        type="command.completed", schema_version=1, attrs_model=CommandCompletedAttrs,
        emitting_process=_EMITTING_PROCESS, record_kind=RecordKind.TASK,
        attention_class=AttentionClass.AMBIENT, intensity=None,
        table=_TABLE, narrate=_narrate_completed,
    ))
    registry.register(EventTypeSpec(
        type="command.failed", schema_version=1, attrs_model=CommandFailedAttrs,
        emitting_process=_EMITTING_PROCESS, record_kind=RecordKind.TASK,
        attention_class=AttentionClass.AMBIENT, intensity=None,
        table=_TABLE, narrate=_narrate_failed,
    ))


_register()
