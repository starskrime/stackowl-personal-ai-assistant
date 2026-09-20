"""Attrs models for the COMMAND-task lifecycle events, and their
registration (Story 4.3, AD-26; ``command.pending_approval`` added Story
4.4, AD-27/AD-28).

One emitting process: ``pipeline.durable`` -- the same durable task loop
``task_events.py`` already emits from, since a COMMAND row IS a ``tasks`` row
(migration 0151's ``kind='command'``), not a second table.
``pipeline/durable/store.py`` is the ONE writer of all four:
``command.enqueued`` inside :meth:`~stackowl.pipeline.durable.store.
DurableTaskStore.create` (its ``kind='command'`` branch), ``command.completed``
inside :meth:`~stackowl.pipeline.durable.store.DurableTaskStore.mark_delivered`
(its ``kind='command'`` branch), ``command.failed`` inside
:meth:`~stackowl.pipeline.durable.store.DurableTaskStore.record_command_failed`
(called from ``commands/spec/submit.py``'s inline-execution failure branches),
``command.pending_approval`` inside :meth:`~stackowl.pipeline.durable.store.
DurableTaskStore.park_for_decision` (called from both this task kind's
callers -- ``submit.py``'s inline path, ``loop.py``'s tick-driven dispatch --
when the action-policy gate decides a command may not run at once)
-- deliberately NOT emitted from ``commands/spec/`` itself, which stays inside
AD-7's import boundary (``authz/`` + ``pipeline/durable`` only) by never
importing ``journal`` directly. All four are the COMMAND TASK WRAPPER's own
lifecycle bookkeeping, never the domain event a mutator's own commit records
(e.g. ``job.paused``) -- AD-26: "the COMMAND handler itself records only
``command.*`` lifecycle events, never the domain event (no double-recording)."
Importing this module registers all four as a side effect; ``journal/
__init__.py`` imports it for exactly that reason.

Reuses ``RecordKind.TASK`` and ``table="tasks"`` rather than declaring a new
``RecordKind`` -- AD-2's own eventual-domain list has no separate "commands"
entry; a COMMAND row is a kind of task, read back by the SAME
``read_task_record`` reader ``task_events.py`` already registers for
``RecordKind.TASK``/``sqlite``.
"""

from __future__ import annotations

from typing import cast

from pydantic import Field

from stackowl.journal.enums import AttentionClass, Intensity, NeedsYouKind, RecordKind
from stackowl.journal.models import JournalAttrsBase
from stackowl.journal.registry import EventTypeSpec, get_registry

_EMITTING_PROCESS = "pipeline.durable"
_TABLE = "tasks"

#: AD-4, verbatim: "attrs hold ids, numbers, closed enums and bounded labels
#: of at most 64 characters" -- mirrors ``task_events.py``'s own bound.
_MAX_LABEL_LEN = 64

#: `CommandPendingApprovalAttrs.payload_summary`'s own bound -- Story 4.4's
#: Code Map, verbatim: "payload summary computed via
#: json.dumps(payload.model_dump(), default=str)[:256]" -- a deliberately
#: WIDER bound than `_MAX_LABEL_LEN`, since this is the deterministic
#: read-back text a Needs-you item shows the owner (AD-27: "rendered
#: deterministically by the narrator from the payload"), not a closed label.
_MAX_PAYLOAD_SUMMARY_LEN = 256


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


class CommandPendingApprovalAttrs(JournalAttrsBase):
    """``command.pending_approval`` (Story 4.4, AD-27/AD-28) -- the
    action-policy gate decided this command may not run at once; the row
    parked and ONE ``approval`` needs_you item opened (or was reused --
    ``record()``'s own NEEDS_YOU wiring dedupes on ``command_id``, so a
    lease-reclaim race that calls :meth:`~stackowl.pipeline.durable.store.
    DurableTaskStore.park_for_decision` twice still opens exactly one).
    ``outcome`` is the gate's own :data:`~stackowl.authz.action_policy.
    ActionPolicyOutcome` (never ``"run_at_once"`` -- that path never parks),
    and ``payload_summary`` is the deterministic, non-model read-back text
    (Code Map: ``json.dumps(payload.model_dump(), default=str)[:256]``)."""

    command_type: str = Field(max_length=_MAX_LABEL_LEN)
    requester_kind: str = Field(max_length=_MAX_LABEL_LEN)
    outcome: str = Field(max_length=_MAX_LABEL_LEN)
    payload_summary: str = Field(max_length=_MAX_PAYLOAD_SUMMARY_LEN)


def _narrate_enqueued(attrs: JournalAttrsBase, name: str) -> str:
    return f"Command {name} was queued."


def _narrate_completed(attrs: JournalAttrsBase, name: str) -> str:
    return f"Command {name} completed."


def _narrate_failed(attrs: JournalAttrsBase, name: str) -> str:
    return f"Command {name} failed."


def _narrate_pending_approval(attrs: JournalAttrsBase, name: str) -> str:  # noqa: ARG001 -- `name` is the task_id (no per-command narrator exists yet, same precedent as command.enqueued/completed/failed above)
    a = cast(CommandPendingApprovalAttrs, attrs)
    return f"Command {a.command_type} needs your decision: {a.payload_summary}"


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
    registry.register(EventTypeSpec(
        type="command.pending_approval", schema_version=1,
        attrs_model=CommandPendingApprovalAttrs,
        emitting_process=_EMITTING_PROCESS, record_kind=RecordKind.TASK,
        attention_class=AttentionClass.NEEDS_YOU, intensity=Intensity.NORMAL,
        needs_you_kind=NeedsYouKind.APPROVAL,
        table=_TABLE, narrate=_narrate_pending_approval,
    ))


_register()
