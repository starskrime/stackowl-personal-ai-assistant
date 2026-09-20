"""``request_undo`` -- submits a completed reversible command's undo through
the one door (Story 4.5, FR31/FR88, AD-1/AD-26/AD-27).

A channel that rendered a completed reversible command's result (Telegram
action button, a future Bridge card) calls this with the ORIGINAL command's
``command_id``. Two outcomes, never a raised exception for an expected
refusal (mirrors ``submit_command``'s own "outcome is None only when..."
convention rather than exception-based control flow for an everyday case):

* ``UndoOutcome(submission=...)`` -- the gate (``authz.undo.decide_undo``)
  allowed it, and the declared ``undo_command_type`` was submitted through
  :func:`~stackowl.commands.spec.submit.submit_command` -- THE SAME ONE DOOR
  every other command walks through (AD-1), running the full severity check
  and action-policy gate again for the undo itself.
* ``UndoOutcome(refusal=...)`` -- refused, either because the original
  command cannot be undone at all (not found, not completed, not reversible)
  or because FR88's own window closed (superseded or 24h passed).

PURPOSELY DOES NO DIRECT JOURNAL I/O (AD-7: this package imports only
``authz/`` + ``pipeline/durable``, never ``journal`` directly) -- the one
FR88 refusal this module itself decides on (superseded/expired) is recorded
via ``pipeline/durable/store.py::record_undo_refused``, exactly the split
``execute.py``/``submit.py`` already use for ``command.failed``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from stackowl.authz.undo import UndoDecision, decide_undo
from stackowl.commands.spec.registry import CommandSpecRegistry
from stackowl.commands.spec.submit import CommandSubmission, submit_command
from stackowl.infra.observability import log
from stackowl.pipeline.durable.store import DurableTaskNotFoundError, DurableTaskStore

if TYPE_CHECKING:  # pragma: no cover -- typing only, never imported at runtime
    from stackowl.db.pool import DbPool


@dataclass(frozen=True)
class UndoRefusal:
    """The AC's own literal shape (Story 4.5 AC3): "the gate refuses it with
    ``{code, reason, remedy}``"."""

    code: str
    reason: str
    remedy: str


@dataclass(frozen=True)
class UndoOutcome:
    """What :func:`request_undo` decided. Exactly one of ``submission``/
    ``refusal`` is set."""

    submission: CommandSubmission | None = None
    refusal: UndoRefusal | None = None


_NOT_FOUND = UndoRefusal(
    code="not_found",
    reason="no completed command exists with that id",
    remedy="check the command id and try again",
)
_NOT_COMPLETED = UndoRefusal(
    code="not_completed",
    reason="this command has not completed yet",
    remedy="wait for it to finish, then try again",
)
_NOT_REVERSIBLE = UndoRefusal(
    code="not_reversible",
    reason="this command has no undo",
    remedy="there is nothing to undo — issue a new command instead",
)


async def request_undo(db: DbPool, command_id: str) -> UndoOutcome:
    """Undo the completed command *command_id* names, if FR88 still allows it.

    Raises :class:`~stackowl.commands.spec.errors.CommandTypeNotDeclaredError`
    only for the genuine invariant violation of a command row whose own
    ``command_type`` no longer resolves in the registry (AD-1: no fallback
    dispatch) -- every ordinary "can't undo this" case returns a
    :class:`UndoRefusal` instead.
    """
    # 1. ENTRY
    log.tasks.debug(
        "[commands] undo.request_undo: entry",
        extra={"_fields": {"command_id": command_id}},
    )
    store = DurableTaskStore(db)
    try:
        original = await store.get_by_command_id(command_id)
    except DurableTaskNotFoundError:
        log.tasks.info(
            "[commands] undo.request_undo: no command found for id",
            extra={"_fields": {"command_id": command_id}},
        )
        return UndoOutcome(refusal=_NOT_FOUND)
    delivered_at = original.delivered_at
    if original.kind != "command" or original.status != "completed" or delivered_at is None:
        log.tasks.info(
            "[commands] undo.request_undo: not undoable (not a completed command)",
            extra={"_fields": {
                "command_id": command_id, "kind": original.kind, "status": original.status,
            }},
        )
        return UndoOutcome(refusal=_NOT_COMPLETED)
    command_type = original.command_type or ""
    spec = CommandSpecRegistry.get(command_type)
    if not spec.reversible or not spec.undo_command_type:
        log.tasks.info(
            "[commands] undo.request_undo: command is not reversible",
            extra={"_fields": {"command_id": command_id, "command_type": command_type}},
        )
        return UndoOutcome(refusal=_NOT_REVERSIBLE)
    # 2. DECISION -- gather the two facts the pure gate needs (FR88), then decide.
    now = datetime.now(UTC)
    elapsed = now - delivered_at
    payload = original.command_payload or "{}"
    # Story 4.7 -- a command type whose payload carries more than its
    # target's identity (an edit, a snooze) can never repeat as an identical
    # ``command_payload`` string across two calls on the SAME job, so
    # supersession there is asked by job_id instead of exact-payload
    # equality. ``json.loads`` never raises here: every registered payload is
    # a validated pydantic model dumped via ``model_dump_json``, always valid
    # JSON. A payload with no ``job_id`` key (a future non-scheduling command
    # type) yields ``None`` and the exact-payload match applies, unchanged.
    target_job_id = json.loads(payload).get("job_id")
    superseded = await store.has_later_completed_command(
        payload=payload, after=delivered_at, exclude_task_id=original.task_id,
        target_job_id=target_job_id,
    )
    decision: UndoDecision = decide_undo(elapsed=elapsed, superseded=superseded)
    if not decision.allowed:
        refusal = UndoRefusal(
            code=decision.code or "refused",
            reason=decision.reason or "",
            remedy=decision.remedy or "",
        )
        await store.record_undo_refused(
            original.task_id, command_type=command_type,
            code=refusal.code, reason=refusal.reason,
        )
        log.tasks.info(
            "[commands] undo.request_undo: refused",
            extra={"_fields": {"command_id": command_id, "code": refusal.code}},
        )
        return UndoOutcome(refusal=refusal)
    # 3. STEP -- submit the declared undo command through the SAME one door
    # (AD-1) every other command walks through. Story 4.7 -- if the original
    # command's own mutator captured a "restore-to" payload
    # (`command_receipts.undo_payload`, e.g. an edit's PRIOR
    # {schedule, goal}), resubmit THAT instead of the original's forward
    # payload — otherwise (every 4.3/4.5/4.6 command, and any 4.7 command
    # whose undo simply re-runs the opposite type, e.g. pause<->resume) fall
    # back to the original's own payload, byte-identical to before this
    # column existed.
    undo_payload = await store.get_undo_payload(command_id)
    resubmit_payload = json.loads(undo_payload) if undo_payload is not None else json.loads(payload)
    submission = await submit_command(
        db, spec.undo_command_type, resubmit_payload,
    )
    # 4. EXIT
    log.tasks.info(
        "[commands] undo.request_undo: exit — undo submitted",
        extra={"_fields": {
            "command_id": command_id, "undo_command_type": spec.undo_command_type,
        }},
    )
    return UndoOutcome(submission=submission)
