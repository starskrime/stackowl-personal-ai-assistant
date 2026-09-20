"""``messaging.*``/``notifications.*`` ``CommandSpec``s -- the outbound-delivery
seam (Story 4.8, AD-1/AD-26).

The census (``authz/state_change_census.py``) named exactly 4 pending entries
this story owns: ``messaging.send_message``, ``messaging.send_file``,
``notifications.broadcast_urgent``, ``notifications.deliver_brief``. This
module also declares 3 MORE types the census never tracked (job handlers are
outside its own tool/slash-command aperture -- see this module's own
Design Notes reference in the story spec): ``notifications.deliver_check_in``,
``notifications.deliver_goal_result``, ``notifications.deliver_digest``.

ALL SEVEN are ``severity="write"``, ``reversible=False`` -- a message, once
sent, cannot be unsent. That makes every one of them subject to
``authz.action_policy.decide``'s "irreversible always needs step-up unless an
unattended run carries a matching standing-authority grant" rule, with zero
owner carve-out (Design Notes: "the gate's honest behavior").

PLACEMENT: here, not ``commands/spec/`` -- AD-7 restricts what
``commands/spec/`` may import (``authz/`` + ``pipeline/durable`` only,
tripwire-enforced); a SUBSYSTEM declaring its own commands is not restricted
the other way, and this module freely imports both ``commands.spec`` (to
register into) and ``notifications``/``scheduler`` (the mutators it wraps) --
mirrors ``scheduler/commands.py``/``objectives/commands.py``'s own placement
rationale exactly, just for the ``notifications``/``messaging`` domain.

RECEIPT SHAPE (Design Notes: "the receipt is written LAST, after the real
send"). Every handler below: (1) a READ-ONLY ``command_receipts`` check FIRST
-- a found receipt means already-attempted, treated as done, no re-send; (2)
the real send/transport, which NEVER raises (B5 -- ``ProactiveDeliverer``'s own
contract); (3) ``record_command_execution`` LAST, unconditionally, once the
terminal ``DeliveryStatus``/rollup is known -- whether that outcome was a
success or a genuine transport failure. A message send is an external,
non-transactional side effect (unlike a pure DB mutation): there is no way to
commit "receipt + send" atomically, so checking first and writing last is what
makes a lease-reclaim retry a no-op rather than a duplicate send (mirrors
``objectives/commands.py``'s own post-review fix for its git-branch step).

Importing this module registers all seven ``CommandSpec``s and handlers as a
side effect (mirrors ``scheduler/commands.py``'s own shape) -- imported once
from ``startup/orchestrator.py``, beside ``objectives.commands``/
``scheduler.commands``.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from stackowl.commands.spec.command_spec import CommandSpec
from stackowl.commands.spec.context import CommandContext, CommandOutcome
from stackowl.commands.spec.handlers import CommandHandlerRegistry
from stackowl.commands.spec.idempotency import record_command_execution
from stackowl.commands.spec.registry import CommandSpecRegistry
from stackowl.commands.spec.submit import CommandSubmission
from stackowl.infra.observability import log
from stackowl.notifications.deliverer import clamp_agent_urgency
from stackowl.notifications.proactive_job import (
    ProactiveDeliveryOutcome,
    ProactiveJobDeliverer,
    job_success_for_rollup,
)
from stackowl.notifications.router import Notification
from stackowl.scheduler.job import Job

SEND_MESSAGE = "messaging.send_message"
SEND_FILE = "messaging.send_file"
BROADCAST_URGENT = "notifications.broadcast_urgent"
DELIVER_BRIEF = "notifications.deliver_brief"
DELIVER_CHECK_IN = "notifications.deliver_check_in"
DELIVER_GOAL_RESULT = "notifications.deliver_goal_result"
DELIVER_DIGEST = "notifications.deliver_digest"

#: A parked/lost-claim ``submission.outcome is None`` reads as this rollup for
#: the 3 job-delivery translators below -- not a real transport status, but
#: ``job_success_for_rollup`` already treats anything outside its known-good
#: set as ``False`` (fail-closed), so the caller's own retry logic re-attempts
#: on its next scheduled run exactly as it would for a genuine transport
#: failure.
_NEEDS_APPROVAL_ROLLUP = "needs_approval"

#: Review fix -- ``command_receipts`` records ONLY that a command was
#: attempted (``command_id``, ``command_type``, ``executed_at``), never its
#: outcome (Design Notes: the receipt is written LAST, unconditionally,
#: whether the send succeeded or genuinely failed). A replay that finds an
#: existing receipt therefore genuinely does NOT know whether the first
#: attempt delivered or failed -- asserting "delivered"/success here would be
#: exactly the overclaim this story's own read-check-first design exists to
#: avoid on the OTHER side (a duplicate send). ``success=False`` lets the
#: command escalate through the existing retry/dead-letter ladder (visible,
#: never a silent false "done") instead of asserting an unconfirmed delivery.
_ALREADY_ATTEMPTED_ERROR = (
    "command already attempted once — the original delivery outcome was not "
    "recorded and is not reconfirmed by this call"
)
_UNKNOWN_DELIVERY_STATUS = "unknown"


# =============================================================================
# messaging.send_message / messaging.send_file
# =============================================================================


class SendMessagePayload(BaseModel):
    """``messaging.send_message``'s payload -- everything ``send_message.
    _deliver`` has ALREADY resolved by the time it submits (the recipient
    chat id included -- resolving it needs the originating ``session_key``,
    which lives only in the caller's ``TraceContext``, not in a durable
    command payload)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    message: str = Field(min_length=1)
    channel: str = Field(min_length=1)
    category: str = "agent_message"
    notification_id: str | None = None
    target: str | int | None = None


class SendFilePayload(BaseModel):
    """``messaging.send_file``'s payload -- mirrors :class:`SendMessagePayload`
    plus the already-validated, already-contained workspace file path."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    file_path: str = Field(min_length=1)
    caption: str = ""
    channel: str = Field(min_length=1)
    category: str = "agent_file"
    notification_id: str | None = None
    target: str | int | None = None


async def _already_executed(db: Any, command_id: str) -> bool:
    """Read-only ``command_receipts`` check -- no side effect (Design Notes:
    "read-check-first")."""
    rows = await db.fetch_all(
        "SELECT 1 FROM command_receipts WHERE command_id = ?", (command_id,),
    )
    return bool(rows)


async def _send_message_handler(
    payload: SendMessagePayload, context: CommandContext,
) -> CommandOutcome:
    from stackowl.pipeline.services import get_services

    db = get_services().db_pool
    if db is None:
        log.notifications.warning(
            "[commands] notifications.commands._send_message_handler: no db pool",
            extra={"_fields": {"channel": payload.channel}},
        )
        return CommandOutcome(
            success=False, error="messaging unavailable (no database configured)",
        )
    deliverer = get_services().proactive_deliverer
    if deliverer is None:
        log.notifications.warning(
            "[commands] notifications.commands._send_message_handler: no "
            "deliverer wired",
            extra={"_fields": {"channel": payload.channel}},
        )
        return CommandOutcome(
            success=False, error="messaging unavailable (no deliverer configured)",
        )

    if await _already_executed(db, context.command_id):
        log.notifications.warning(
            "[commands] notifications.commands._send_message_handler: command "
            "already attempted once — outcome not reconfirmed by this call",
            extra={"_fields": {"command_id": context.command_id}},
        )
        return CommandOutcome(
            success=False, error=_ALREADY_ATTEMPTED_ERROR,
            result={"delivery_status": _UNKNOWN_DELIVERY_STATUS, "already_sent": True},
        )

    notification = Notification(
        message=payload.message,
        urgency=clamp_agent_urgency("normal"),
        category=payload.category,
        channel_name=payload.channel,
        notification_id=payload.notification_id,
        target=payload.target,
    )
    status = await deliverer.deliver(notification, context=context)

    # THE RECEIPT WRITE, LAST — after the real (non-transactional) send,
    # regardless of whether it succeeded (Design Notes).
    async with db.transaction() as conn:
        await record_command_execution(conn, context.command_id, context.command_type)

    if status == "failed":
        log.notifications.warning(
            "[commands] notifications.commands._send_message_handler: delivery "
            "failed",
            extra={"_fields": {"channel": payload.channel}},
        )
        return CommandOutcome(
            success=False,
            error="delivery failed — the transport could not deliver the "
                  "message after retry",
            result={"delivery_status": status},
        )
    return CommandOutcome(success=True, result={"delivery_status": status})


async def _send_file_handler(
    payload: SendFilePayload, context: CommandContext,
) -> CommandOutcome:
    from stackowl.pipeline.services import get_services

    db = get_services().db_pool
    if db is None:
        log.notifications.warning(
            "[commands] notifications.commands._send_file_handler: no db pool",
            extra={"_fields": {"channel": payload.channel}},
        )
        return CommandOutcome(
            success=False, error="messaging unavailable (no database configured)",
        )
    deliverer = get_services().proactive_deliverer
    if deliverer is None:
        log.notifications.warning(
            "[commands] notifications.commands._send_file_handler: no "
            "deliverer wired",
            extra={"_fields": {"channel": payload.channel}},
        )
        return CommandOutcome(
            success=False, error="messaging unavailable (no deliverer configured)",
        )

    if await _already_executed(db, context.command_id):
        log.notifications.warning(
            "[commands] notifications.commands._send_file_handler: command "
            "already attempted once — outcome not reconfirmed by this call",
            extra={"_fields": {"command_id": context.command_id}},
        )
        return CommandOutcome(
            success=False, error=_ALREADY_ATTEMPTED_ERROR,
            result={"delivery_status": _UNKNOWN_DELIVERY_STATUS, "already_sent": True},
        )

    notification = Notification(
        message=payload.caption,
        urgency=clamp_agent_urgency("normal"),
        category=payload.category,
        channel_name=payload.channel,
        notification_id=payload.notification_id,
        file_path=payload.file_path,
        target=payload.target,
    )
    status = await deliverer.deliver(notification, context=context)

    async with db.transaction() as conn:
        await record_command_execution(conn, context.command_id, context.command_type)

    if status == "failed":
        log.notifications.warning(
            "[commands] notifications.commands._send_file_handler: delivery "
            "failed",
            extra={"_fields": {"channel": payload.channel}},
        )
        return CommandOutcome(
            success=False,
            error="delivery failed — the transport could not deliver the "
                  "file after retry",
            result={"delivery_status": status},
        )
    return CommandOutcome(success=True, result={"delivery_status": status})


# =============================================================================
# notifications.broadcast_urgent
# =============================================================================


class BroadcastUrgentPayload(BaseModel):
    """``notifications.broadcast_urgent``'s payload -- ``/urgent``'s already-
    resolved channel roster (live at dispatch time, per ``UrgentCommand.
    _resolve_channels``) plus the message to broadcast."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    message: str = Field(min_length=1)
    channels: list[str] = Field(default_factory=list)


_URGENT_CATEGORY = "user_urgent"


async def _broadcast_urgent_handler(
    payload: BroadcastUrgentPayload, context: CommandContext,
) -> CommandOutcome:
    import asyncio

    from stackowl.pipeline.services import get_services

    db = get_services().db_pool
    if db is None:
        log.notifications.warning(
            "[commands] notifications.commands._broadcast_urgent_handler: no "
            "db pool",
        )
        return CommandOutcome(
            success=False, error="messaging unavailable (no database configured)",
        )
    deliverer = get_services().proactive_deliverer
    if deliverer is None:
        log.notifications.warning(
            "[commands] notifications.commands._broadcast_urgent_handler: no "
            "deliverer wired",
        )
        return CommandOutcome(
            success=False, error="messaging unavailable (no deliverer configured)",
        )

    total = len(payload.channels)
    if await _already_executed(db, context.command_id):
        log.notifications.warning(
            "[commands] notifications.commands._broadcast_urgent_handler: "
            "command already attempted once — outcome not reconfirmed by this call",
            extra={"_fields": {"command_id": context.command_id}},
        )
        return CommandOutcome(
            success=False, error=_ALREADY_ATTEMPTED_ERROR,
            result={"total": total, "already_sent": True},
        )

    notifications = [
        Notification(
            message=payload.message, urgency="critical",
            category=_URGENT_CATEGORY, channel_name=ch,
        )
        for ch in payload.channels
    ]
    results = await asyncio.gather(
        *(deliverer.deliver(n, context=context) for n in notifications),
        return_exceptions=True,
    )
    delivered = 0
    failed = 0
    for res in results:
        if isinstance(res, BaseException) or res != "delivered":
            failed += 1
            if isinstance(res, BaseException):
                log.notifications.warning(
                    "[commands] notifications.commands._broadcast_urgent_handler:"
                    " transport raised", exc_info=res,
                )
        else:
            delivered += 1

    async with db.transaction() as conn:
        await record_command_execution(conn, context.command_id, context.command_type)

    log.notifications.info(
        "[commands] notifications.commands._broadcast_urgent_handler: exit",
        extra={"_fields": {"delivered": delivered, "failed": failed, "total": total}},
    )
    # Review fix — zero resolved channels is NOT trivially successful: nothing
    # was configured to receive the broadcast, so nothing was ever broadcast.
    if total == 0:
        success = False
        error: str | None = "no channel configured to receive the broadcast"
    else:
        success = delivered > 0
        error = None if success else "no channel received the broadcast"
    return CommandOutcome(
        success=success,
        result={"delivered": delivered, "failed": failed, "total": total},
        error=error,
    )


# =============================================================================
# notifications.deliver_brief / deliver_check_in / deliver_goal_result
# =============================================================================


class DeliverBriefPayload(BaseModel):
    """``notifications.deliver_brief``'s payload -- carries the FULL ``Job``
    (not just its id) so this ONE command type covers both the scheduled
    morning-brief job AND ``/brief``'s synthetic, non-persisted ad-hoc job
    (Code Map: "one command type covers both triggers") -- a DB lookup by
    job_id would find nothing for the ad-hoc case."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    job: Job
    message: str
    category: str = "morning_brief"


class DeliverCheckInPayload(BaseModel):
    """``notifications.deliver_check_in``'s payload -- mirrors
    :class:`DeliverBriefPayload`, for the check-in job."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    job: Job
    message: str
    category: str = "check_in"


class DeliverGoalResultPayload(BaseModel):
    """``notifications.deliver_goal_result``'s payload -- mirrors
    :class:`DeliverBriefPayload`, for a goal-execution job's answer.
    ``urgency`` carries ``goal_execution._deliver_answer``'s own TS10
    critical/normal distinction (a one-shot ``run_once`` goal vs. a recurring
    scheduled poke)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    job: Job
    message: str
    category: str = "goal_answer"
    urgency: str = "normal"


def outcome_from_submission(submission: CommandSubmission) -> ProactiveDeliveryOutcome:
    """Reconstruct an honest :class:`ProactiveDeliveryOutcome` from what
    ``submit_command`` returned for a job-scoped ``notifications.deliver_*``
    command (Story 4.8) -- shared by ``morning_brief``/``check_in``/
    ``goal_execution``'s own ``_deliver``/``_deliver_answer`` so each keeps its
    existing rollup-based downstream logic (``_record_result``,
    ``job_success_for_rollup``) unchanged.

    ``submission.outcome is None`` means the command PARKED awaiting a
    decision (the action-policy gate refused to run it at once -- e.g. no
    matching standing authority) or lost the inline claim to another worker;
    either way nothing was delivered on THIS call, so the honest rollup is
    :data:`_NEEDS_APPROVAL_ROLLUP` -- a status ``job_success_for_rollup``
    already treats as not delivered (fail-closed), so the job's own retry
    logic re-attempts on its next scheduled run.
    """
    if submission.outcome is None:
        return ProactiveDeliveryOutcome(rollup=_NEEDS_APPROVAL_ROLLUP)
    result = submission.outcome.result
    if "rollup" not in result:
        # The command's own severity/handler-dispatch refusal, never a real
        # transport attempt (e.g. no db/deliverer wired) — echo it as a plain
        # failed rollup rather than fabricate a per-channel shape.
        return ProactiveDeliveryOutcome(rollup="failed" if not submission.outcome.success else "delivered")
    return ProactiveDeliveryOutcome(
        rollup=str(result.get("rollup") or "failed"),
        per_channel=dict(result.get("per_channel") or {}),
        undeliverable=tuple(result.get("undeliverable") or ()),
        suppressed_replay=tuple(result.get("suppressed_replay") or ()),
    )


async def _deliver_via_job_deliverer(
    *, job: Job, message: str, category: str, urgency: str, context: CommandContext,
) -> CommandOutcome:
    """Shared body for the 3 job-scoped delivery handlers below -- differs
    only in which payload fields fed it."""
    from stackowl.notifications.delivery_ledger import DeliveryLedger
    from stackowl.pipeline.services import get_services

    services = get_services()
    db = services.db_pool
    if db is None:
        log.notifications.warning(
            "[commands] notifications.commands._deliver_via_job_deliverer: no "
            "db pool",
            extra={"_fields": {"job_id": job.job_id, "category": category}},
        )
        return CommandOutcome(
            success=False, error="delivery unavailable (no database configured)",
        )
    deliverer = services.proactive_deliverer
    if deliverer is None:
        log.notifications.warning(
            "[commands] notifications.commands._deliver_via_job_deliverer: no "
            "deliverer wired",
            extra={"_fields": {"job_id": job.job_id, "category": category}},
        )
        return CommandOutcome(
            success=False, error="delivery unavailable (no deliverer configured)",
        )

    if await _already_executed(db, context.command_id):
        log.notifications.warning(
            "[commands] notifications.commands._deliver_via_job_deliverer: "
            "command already attempted once — outcome not reconfirmed by this call",
            extra={"_fields": {"command_id": context.command_id, "job_id": job.job_id}},
        )
        return CommandOutcome(
            success=False, error=_ALREADY_ATTEMPTED_ERROR,
            result={"rollup": _UNKNOWN_DELIVERY_STATUS, "already_sent": True},
        )

    job_deliverer = ProactiveJobDeliverer(deliverer, DeliveryLedger(db), settings=services.settings)
    outcome = await job_deliverer.deliver_for_job(
        job, message=message, category=category, urgency=urgency, context=context,
    )

    async with db.transaction() as conn:
        await record_command_execution(conn, context.command_id, context.command_type)

    success = job_success_for_rollup(outcome.rollup)
    return CommandOutcome(
        success=success,
        result={
            "rollup": outcome.rollup,
            "per_channel": dict(outcome.per_channel),
            "undeliverable": list(outcome.undeliverable),
            "suppressed_replay": list(outcome.suppressed_replay),
        },
        error=None if success else f"delivery rollup={outcome.rollup}",
    )


async def _deliver_brief_handler(
    payload: DeliverBriefPayload, context: CommandContext,
) -> CommandOutcome:
    return await _deliver_via_job_deliverer(
        job=payload.job, message=payload.message, category=payload.category,
        urgency="normal", context=context,
    )


async def _deliver_check_in_handler(
    payload: DeliverCheckInPayload, context: CommandContext,
) -> CommandOutcome:
    return await _deliver_via_job_deliverer(
        job=payload.job, message=payload.message, category=payload.category,
        urgency="normal", context=context,
    )


async def _deliver_goal_result_handler(
    payload: DeliverGoalResultPayload, context: CommandContext,
) -> CommandOutcome:
    return await _deliver_via_job_deliverer(
        job=payload.job, message=payload.message, category=payload.category,
        urgency=payload.urgency, context=context,
    )


# =============================================================================
# notifications.deliver_digest
# =============================================================================


class DeliverDigestPayload(BaseModel):
    """``notifications.deliver_digest``'s payload -- the digest flush's own
    per-queue-row send: a plain ``transport(channel, message)``, never
    ``deliver_for_job`` (the routing decision was already made when the
    notification was first batched -- ``digest_job.py``'s own module
    docstring). ``job_id`` is the digest job's own real (persisted) id,
    carried for traceability only -- the handler needs no job lookup."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    job_id: str = Field(min_length=1)
    channel: str = Field(min_length=1)
    message: str = Field(min_length=1)


async def _deliver_digest_handler(
    payload: DeliverDigestPayload, context: CommandContext,
) -> CommandOutcome:
    from stackowl.pipeline.services import get_services

    db = get_services().db_pool
    if db is None:
        log.notifications.warning(
            "[commands] notifications.commands._deliver_digest_handler: no db pool",
            extra={"_fields": {"job_id": payload.job_id, "channel": payload.channel}},
        )
        return CommandOutcome(
            success=False, error="delivery unavailable (no database configured)",
        )
    deliverer = get_services().proactive_deliverer
    if deliverer is None:
        log.notifications.warning(
            "[commands] notifications.commands._deliver_digest_handler: no "
            "deliverer wired",
            extra={"_fields": {"job_id": payload.job_id, "channel": payload.channel}},
        )
        return CommandOutcome(
            success=False, error="delivery unavailable (no deliverer configured)",
        )

    if await _already_executed(db, context.command_id):
        log.notifications.warning(
            "[commands] notifications.commands._deliver_digest_handler: command "
            "already attempted once — outcome not reconfirmed by this call",
            extra={"_fields": {"command_id": context.command_id}},
        )
        return CommandOutcome(
            success=False, error=_ALREADY_ATTEMPTED_ERROR,
            result={"delivery_status": _UNKNOWN_DELIVERY_STATUS, "already_sent": True},
        )

    status = await deliverer.transport(payload.channel, payload.message, context=context)

    async with db.transaction() as conn:
        await record_command_execution(conn, context.command_id, context.command_type)

    if status == "failed":
        return CommandOutcome(
            success=False, error="transport failed", result={"delivery_status": status},
        )
    return CommandOutcome(success=True, result={"delivery_status": status})


def _register() -> None:
    send_message_spec = CommandSpec(
        command_type=SEND_MESSAGE,
        payload_model=SendMessagePayload,
        severity="write",
        # Irreversible: a sent message cannot be unsent (Design Notes: this
        # forces step-up for EVERY requester kind, including the owner — the
        # gate's honest, unmodified behavior for a genuinely one-way action).
        reversible=False,
    )
    send_file_spec = CommandSpec(
        command_type=SEND_FILE,
        payload_model=SendFilePayload,
        severity="write",
        reversible=False,
    )
    broadcast_urgent_spec = CommandSpec(
        command_type=BROADCAST_URGENT,
        payload_model=BroadcastUrgentPayload,
        severity="write",
        reversible=False,
    )
    deliver_brief_spec = CommandSpec(
        command_type=DELIVER_BRIEF,
        payload_model=DeliverBriefPayload,
        severity="write",
        reversible=False,
    )
    deliver_check_in_spec = CommandSpec(
        command_type=DELIVER_CHECK_IN,
        payload_model=DeliverCheckInPayload,
        severity="write",
        reversible=False,
    )
    deliver_goal_result_spec = CommandSpec(
        command_type=DELIVER_GOAL_RESULT,
        payload_model=DeliverGoalResultPayload,
        severity="write",
        reversible=False,
    )
    deliver_digest_spec = CommandSpec(
        command_type=DELIVER_DIGEST,
        payload_model=DeliverDigestPayload,
        severity="write",
        reversible=False,
    )
    CommandSpecRegistry.register(send_message_spec)
    CommandSpecRegistry.register(send_file_spec)
    CommandSpecRegistry.register(broadcast_urgent_spec)
    CommandSpecRegistry.register(deliver_brief_spec)
    CommandSpecRegistry.register(deliver_check_in_spec)
    CommandSpecRegistry.register(deliver_goal_result_spec)
    CommandSpecRegistry.register(deliver_digest_spec)
    CommandHandlerRegistry.register(SEND_MESSAGE, _send_message_handler)  # type: ignore[arg-type]
    CommandHandlerRegistry.register(SEND_FILE, _send_file_handler)  # type: ignore[arg-type]
    CommandHandlerRegistry.register(BROADCAST_URGENT, _broadcast_urgent_handler)  # type: ignore[arg-type]
    CommandHandlerRegistry.register(DELIVER_BRIEF, _deliver_brief_handler)  # type: ignore[arg-type]
    CommandHandlerRegistry.register(DELIVER_CHECK_IN, _deliver_check_in_handler)  # type: ignore[arg-type]
    CommandHandlerRegistry.register(DELIVER_GOAL_RESULT, _deliver_goal_result_handler)  # type: ignore[arg-type]
    CommandHandlerRegistry.register(DELIVER_DIGEST, _deliver_digest_handler)  # type: ignore[arg-type]

    log.notifications.info(
        "[commands] notifications.commands: CommandSpecs registered",
        extra={"_fields": {"command_types": [
            SEND_MESSAGE, SEND_FILE, BROADCAST_URGENT, DELIVER_BRIEF,
            DELIVER_CHECK_IN, DELIVER_GOAL_RESULT, DELIVER_DIGEST,
        ]}},
    )


_register()
