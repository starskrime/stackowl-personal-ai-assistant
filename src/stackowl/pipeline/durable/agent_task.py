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
_DELIVERED_STATUSES = frozenset({"completed", "delivered", "suppressed"})


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
    if delivery_status not in _DELIVERED_STATUSES:
        log.tasks.info(
            "[loop] scheduled task produced an answer that did NOT reach its "
            "destination — leaving it open for the loop",
            extra={"_fields": {"task_id": task_id, "delivery": delivery_status}},
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
