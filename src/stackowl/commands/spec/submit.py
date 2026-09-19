"""``submit_command`` -- the ONE typed submit entry (Story 4.3, AD-1).

Every mutating surface/tool calls this, never a subsystem mutator directly.
Validates the payload against the declared :class:`CommandSpec`, sets
``requester_kind`` from ingress/trace provenance (never the caller's payload
dict), enqueues a ``kind='command'`` row keyed by ``command_id``, and then —
mirroring ``pipeline/durable/store.py::create_child_task``'s
"insert-or-claim-or-existing" precedent and ``claim_child_lease``'s
CAS-claim-then-execute-inline pattern — claims that SAME row inline and runs
it immediately through :func:`~stackowl.commands.spec.execute.
execute_command_task`, so a synchronous caller (e.g. the ``cronjob`` tool)
gets back an honest, real outcome rather than "queued, check back later".

``run_inline=False`` is the OTHER half: a caller with no local ``TaskLoop`` to
claim against (a gateway-role process, which has no in-process loop to wake —
``pipeline/durable/turn_task.py``'s own ``loop.wake()`` precedent only works
when the enqueuer and the loop share a process) enqueues the row and stops
there; ``notify_enqueued`` (typed generically, never ``ipc.frames`` or
``pipeline.services`` — this package's AD-7 import boundary forbids both) lets
that caller supply its own "tell the other side" callback, e.g. sending a
``TasksEnqueuedFrame`` over its live gateway<->core connection. The
tick-driven ``TaskLoop`` is the fallback either way.

No generic ``execute_command(command_type, **kwargs)`` entry point exists
anywhere in this module or this package (AD-1's Boundaries) — the only way
in is this one function, and the only way it runs anything is through the
closed :class:`~stackowl.commands.spec.registry.CommandSpecRegistry`/
:class:`~stackowl.commands.spec.handlers.CommandHandlerRegistry` dicts.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from stackowl.authz.requester import requester_kind_from_trace
from stackowl.commands.spec.context import CommandOutcome
from stackowl.commands.spec.errors import CommandRefusedError
from stackowl.commands.spec.execute import execute_command_task
from stackowl.commands.spec.registry import CommandSpecRegistry

# AD-7 — see registry.py's identical import for why `log` is allowed here.
from stackowl.infra.observability import log
from stackowl.pipeline.durable.failure_class import classify_failure

# `DurableTaskNotFoundError` is `stackowl.exceptions`' own class, re-exported
# here via `pipeline.durable.store` (which already imports it for itself) —
# same "reuse the allowed module's own import" shape as this package's `log`.
from stackowl.pipeline.durable.store import DurableTaskNotFoundError, DurableTaskStore
from stackowl.pipeline.durable.task import DurableTask

if TYPE_CHECKING:  # pragma: no cover -- typing only, never imported at runtime
    from stackowl.db.pool import DbPool

#: A UNIQUE-constraint violation on `command_id` (idx_tasks_command_id,
#: migration 0151) always mentions both of these substrings in aiosqlite's
#: exception text — mirrors `pipeline/durable/turn_task.py`'s own
#: "distinguished by the constraint name, not by catching IntegrityError
#: broadly" precedent, so an UNRELATED unique violation keeps surfacing loudly.
_COMMAND_ID_COLLISION_MARKERS = ("UNIQUE constraint failed", "command_id")

#: The two `command.failed`/`fail_and_requeue` buckets this module ever
#: produces — a deterministic severity refusal is a materially different
#: fact from the handler itself failing, and a reader (or a future story's
#: policy) must be able to tell them apart rather than seeing one hardcoded
#: label for both.
_FAILURE_CLASS_REFUSED = "command_refused"
_FAILURE_CLASS_HANDLER_FAILED = "command_handler_failed"


@dataclass(frozen=True)
class CommandSubmission:
    """What ``submit_command`` hands back: the command's identity, and — when
    this call actually ran it inline — the real outcome. ``outcome`` is
    ``None`` only when this call did not itself observe a terminal result
    (lost the inline claim race, replayed an already-*pending* command_id, or
    ran with ``run_inline=False``); the tick-driven ``TaskLoop`` is the
    fallback for all three."""

    command_id: str
    task_id: str
    outcome: CommandOutcome | None = None


async def submit_command(
    db: DbPool,
    command_type: str,
    payload: dict[str, Any] | BaseModel,
    *,
    command_id: str | None = None,
    run_inline: bool = True,
    notify_enqueued: Callable[[], Awaitable[None]] | None = None,
) -> CommandSubmission:
    """Validate, enqueue and (inline, when possible) run one command.

    ``command_id`` lets a caller retry idempotently: passing back the value a
    PRIOR call returned reuses that row instead of enqueuing a second one
    (the I/O matrix's "second call reuses an already-minted command_id" row).
    Left ``None`` (the ordinary case), a fresh id is minted.

    ``run_inline`` (default ``True``) is the existing claim-and-run behavior,
    unchanged — every caller today (``cronjob.py``) keeps it. A caller with
    no local ``TaskLoop`` to claim against passes ``run_inline=False``: the
    row is created and left ``pending`` for the tick-driven loop, and
    ``notify_enqueued`` (if given) is awaited so that loop can be woken
    without waiting out the tick — e.g. a gateway-role caller passing a
    callback that sends ``TasksEnqueuedFrame`` over its core connection.
    Best-effort: a failed notify costs one tick of latency, never the row.

    Raises :class:`~stackowl.commands.spec.errors.CommandTypeNotDeclaredError`
    for an unregistered *command_type* — there is no fallback dispatch (AD-1).
    """
    # 1. ENTRY
    log.tasks.debug(
        "[commands] submit.submit_command: entry",
        extra={"_fields": {
            "command_type": command_type, "retry": command_id is not None,
            "run_inline": run_inline,
        }},
    )
    # Raises CommandTypeNotDeclaredError for an unregistered type — BEFORE
    # anything is validated or written (AD-1: no fallback dispatch).
    spec = CommandSpecRegistry.get(command_type)
    validated = (
        payload if isinstance(payload, spec.payload_model)
        else spec.payload_model.model_validate(payload)
    )
    # AD-1: requester_kind comes from ingress/trace provenance, NEVER from the
    # caller-supplied payload dict — `validated` above is never consulted here.
    requester_kind = requester_kind_from_trace()
    cid = command_id or str(uuid.uuid4())
    task_id = f"cmd-{cid}"
    task = DurableTask(
        task_id=task_id,
        goal=f"command:{command_type}",
        status="pending",
        kind="command",
        command_type=command_type,
        command_payload=validated.model_dump_json(),
        command_id=cid,
        requester_kind=requester_kind,
        trigger_kind="command",
    )
    store = DurableTaskStore(db)
    # 2. DECISION — insert, or (a command_id retry) recognize the collision
    # and reuse the existing row instead of enqueuing a second one.
    try:
        await store.create(task)
    except Exception as exc:
        if not all(marker in str(exc) for marker in _COMMAND_ID_COLLISION_MARKERS):
            raise
        log.tasks.info(
            "[commands] submit.submit_command: command_id already minted "
            "— reusing the existing row",
            extra={"_fields": {"command_id": cid, "command_type": command_type}},
        )
        try:
            existing = await store.get_by_command_id(cid)
        except DurableTaskNotFoundError:
            # The row that collided is gone (pruned between the INSERT
            # failing and this re-fetch) — never surface the raw lookup
            # error for what is, to the caller, an ordinary retry.
            log.tasks.error(
                "[commands] submit.submit_command: command_id collided but "
                "the existing row could not be re-fetched",
                extra={"_fields": {"command_id": cid, "command_type": command_type}},
            )
            return CommandSubmission(command_id=cid, task_id=task_id, outcome=None)
        # A retry against an already-DECIDED command reports the REAL
        # outcome — otherwise it is indistinguishable from "could not
        # confirm it ran" even though it plainly did.
        if existing.status == "completed":
            return CommandSubmission(
                command_id=cid, task_id=existing.task_id,
                outcome=CommandOutcome(
                    success=True, result={"task_id": existing.task_id},
                ),
            )
        if existing.status in ("failed", "dead_letter"):
            return CommandSubmission(
                command_id=cid, task_id=existing.task_id,
                outcome=CommandOutcome(
                    success=False, error=existing.last_error or "command failed",
                ),
            )
        return CommandSubmission(command_id=cid, task_id=existing.task_id, outcome=None)

    if not run_inline:
        # Gateway-role path (AD-1's other half): no local loop to claim
        # against. Leave the row pending and best-effort notify whoever can
        # wake the loop that actually owns it.
        if notify_enqueued is not None:
            try:
                await notify_enqueued()
            except Exception as exc:  # noqa: BLE001 — a failed wake costs latency, never the row
                log.tasks.warning(
                    "[commands] submit.submit_command: notify_enqueued raised "
                    "— the tick loop will still pick this up",
                    exc_info=exc, extra={"_fields": {"command_id": cid}},
                )
        log.tasks.info(
            "[commands] submit.submit_command: exit — enqueued, not run inline",
            extra={"_fields": {"command_id": cid, "command_type": command_type}},
        )
        return CommandSubmission(command_id=cid, task_id=task_id, outcome=None)

    # 3. STEP — claim-then-run-inline (mirrors `claim_child_lease`'s own
    # precedent for inline, non-tick execution). A lost claim (another
    # worker/process won it first) is not an error: the tick-driven TaskLoop
    # is the fallback path for exactly this case.
    claimed = await store.claim(task_id, worker=f"submit-inline-{cid[:8]}")
    if not claimed:
        log.tasks.info(
            "[commands] submit.submit_command: lost the inline claim — the "
            "tick loop will run this command",
            extra={"_fields": {"command_id": cid, "task_id": task_id}},
        )
        return CommandSubmission(command_id=cid, task_id=task_id, outcome=None)
    fresh = await store.get(task_id)
    try:
        outcome = await execute_command_task(fresh)
    except Exception as exc:
        failure_class = (
            _FAILURE_CLASS_REFUSED if isinstance(exc, CommandRefusedError)
            else classify_failure(exc)
        )
        await store.fail_and_requeue(task_id, error=str(exc), failure_class=failure_class)
        await store.record_command_failed(
            task_id, command_type=command_type,
            failure_class=(
                _FAILURE_CLASS_REFUSED if isinstance(exc, CommandRefusedError)
                else _FAILURE_CLASS_HANDLER_FAILED
            ),
        )
        log.tasks.warning(
            "[commands] submit.submit_command: inline execution raised",
            exc_info=exc,
            extra={"_fields": {"command_id": cid, "command_type": command_type}},
        )
        return CommandSubmission(
            command_id=cid, task_id=task_id,
            outcome=CommandOutcome(success=False, error=str(exc)),
        )
    if outcome.success:
        await store.mark_delivered(task_id, result=f"command {command_type} completed")
    else:
        await store.fail_and_requeue(
            task_id, error=outcome.error or "command failed",
            failure_class=_FAILURE_CLASS_HANDLER_FAILED,
        )
        await store.record_command_failed(
            task_id, command_type=command_type,
            failure_class=_FAILURE_CLASS_HANDLER_FAILED,
        )
    # 4. EXIT
    log.tasks.info(
        "[commands] submit.submit_command: exit",
        extra={"_fields": {
            "command_id": cid, "command_type": command_type, "success": outcome.success,
        }},
    )
    return CommandSubmission(command_id=cid, task_id=task_id, outcome=outcome)
