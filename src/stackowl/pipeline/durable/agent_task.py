"""An agent's own scheduled work is a task on the ONE loop, on the same terms.

BAKIR, 2026-08-18: *"loop should not be only for telegram, it should be for all
gateway and all actions what agents done."*

THE GATEWAY HALF was already true — the chat ingress sits in the orchestrator's
shared ``_intake``/``_dispatch_turn`` behind a generic ``_IntakeAdapter``, so
telegram, cli, slack, discord and whatsapp all pass through one seam. Telegram
dominates the live rows only because it is the only channel with traffic.

THE AGENT HALF WAS NOT, and the live data said so plainly. ``goal_execution``
already created durable rows for scheduled work, but with ``trigger_kind``,
``destination`` and ``achievement`` NULL — so the rows read::

    status='completed'   delivered_at=NULL

A task claiming completion with no proof its outcome reached anyone. That is the
overclaim shape this platform keeps paying for, sitting inside the agent's own
actions rather than a chat turn: the morning brief could fail to send and the job
would still report success.

This module gives scheduled work the same two properties a chat turn already has —
a DESTINATION, and completion that means DELIVERED — without duplicating the chat
bridge, because the two differ in exactly one way: a chat turn always has someone
waiting, and a scheduled job may legitimately have nobody (a sweep, a prune). That
difference is why ``describe_job_destination`` may return None, and why a job with
no targets is not held to a delivery it was never asked to make.
"""

from __future__ import annotations

from typing import Any

from stackowl.infra.observability import log

#: Delivery rollups that mean the outcome ACTUALLY reached its destination.
#: Everything else — undeliverable, partial, failed — leaves the task open so the
#: loop can recover it. "suppressed" counts: the router deliberately withheld the
#: message (quiet hours, focus mode), which is a decision about delivery rather
#: than a failure of it.
#:
#: PUBLIC because the chat path asks it too. ``steps/deliver`` needs the same rule
#: for its stream-miss proactive push, and a second copy of this set is exactly the
#: "two copies of one rule" shape this platform keeps paying for.
DELIVERED_STATUSES = frozenset({"completed", "delivered", "suppressed"})


def describe_job_destination(job: Any) -> str | None:
    """Where a scheduled job's answer must land, or ``None`` if nowhere.

    ``None`` is the honest answer for a maintenance job — a sweep, a prune, a
    canary — that produces no user-facing output. Inventing a destination for
    those would hold every housekeeping handler on the platform to a delivery it
    was never asked to make, and dead-letter all of them.
    """
    channels = [str(c) for c in (getattr(job, "target_channels", None) or []) if c]
    if not channels:
        return None
    addresses = [str(a) for a in (getattr(job, "target_addresses", None) or []) if a]
    if not addresses:
        # A single-terminal channel (cli) addresses itself implicitly.
        return ",".join(channels)
    # A broadcast lands in more than one place; say so rather than silently
    # recording only the first, which would make a partial delivery look complete.
    return ",".join(f"{c}:{addresses[0]}" if len(addresses) == 1 else c for c in channels)


async def complete_agent_task(
    store: Any, *, task_id: str, result: str, delivery_status: str,
) -> None:
    """Mark a scheduled task delivered — only when it genuinely was. Never raises.

    ``delivery_status`` is the rollup ``goal_execution._deliver_answer`` already
    computes, which is deliberately honest: it returns "undeliverable" when a body
    existed and nothing was sent, and "partial" when only some channels took it.
    Reusing that verdict rather than deriving a second one is the point — a second
    opinion about whether delivery happened is how the two drift.
    """
    if store is None:
        return
    if not (result or "").strip():
        return
    if delivery_status not in DELIVERED_STATUSES:
        # RETURN IT TO THE LOOP, DO NOT MERELY SAY SO. This used to log "leaving it
        # open for the loop" and return — but the loop claims `status='pending'`
        # and the row is `running`, so nothing could pick it up. The only thing
        # that touches a stale running row is the liveness sweep, and that path
        # re-drives WITHOUT counting attempts: one model call every 600s, forever,
        # with no ceiling and no escalation. Measured 2026-08-20 on a real turn.
        #
        # `fail_and_requeue` already counts the attempt, carries what failed into
        # the next try, stops at the ceiling and dead-letters with a message to the
        # operator. Using it is the sentence the old log line was claiming, not a
        # second mechanism.
        #
        # WHICH KIND OF FAILURE IT IS IS ALREADY DECIDED. `_deliver_answer`'s own
        # contract says "undeliverable → no target, retry won't help" and marks
        # partial/failed for retry. Undeliverable is therefore the `permanent`
        # class: ONE dead-letter and ONE message to the operator, rather than
        # thirty model calls at a channel with nowhere to send. Reusing that
        # verdict rather than deriving a second one is the same rule as above.
        permanent = delivery_status == "undeliverable"
        log.tasks.info(
            "[loop] scheduled task produced an answer that did NOT reach its "
            "destination — returning it to the loop",
            extra={"_fields": {"task_id": task_id, "delivery": delivery_status,
                               "permanent": permanent}},
        )
        try:
            await store.fail_and_requeue(
                task_id,
                error=(
                    f"the answer was produced but delivery reported "
                    f"'{delivery_status}' — it never reached anyone. "
                    + ("The destination has no durable address, so retrying the "
                       "same route cannot help." if permanent else
                       "The transport may recover; the next attempt can try again.")
                ),
                failure_class="permanent" if permanent else "delivery",
            )
        except Exception as exc:
            # The handler owns the JobResult; a bookkeeping failure must not turn a
            # delivery problem into a crashed job. The lease expires and recovery
            # picks the row up, which is the same fallback as below.
            log.tasks.error(
                "[loop] could not return an undelivered scheduled task to the loop",
                exc_info=exc, extra={"_fields": {"task_id": task_id}},
            )
        return
    try:
        await store.mark_delivered(task_id, result=result[:8000])
    except Exception as exc:
        # The answer HAS been delivered; we merely failed to record it. The lease
        # expires and the loop may re-drive — which is why an effectful scheduled
        # job wants an idempotency key.
        log.tasks.error(
            "[loop] could not mark a delivered scheduled task complete — its lease "
            "will expire and the loop may re-drive it",
            exc_info=exc, extra={"_fields": {"task_id": task_id}},
        )
