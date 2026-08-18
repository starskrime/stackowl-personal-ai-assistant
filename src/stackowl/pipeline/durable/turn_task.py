"""A chat turn is a task on the ONE loop (Bakir's slice 3, 2026-08-17).

*"If I am pinging in the Telegram chat about some question, that's also task."*
*"Loop should go understand, find the answer, return back answer to the Telegram.
And if it's delivered to me, it means loop is completed."*

WHAT THIS IS, AND WHAT IT IS NOT — stated here rather than in a commit message,
because the distinction is the whole design:

* Every chat turn gets a DURABLE ROW at ingress, and that row completes only when
  the reply actually reached the user. That makes the loop the authoritative owner
  of every turn, and makes "completed" mean delivered rather than returned.
* The existing pipeline still PRODUCES the reply on the fast path. A working turn's
  latency is unchanged.
* If the reply never lands — the process died, the provider was out, the send
  failed — the row's lease expires and the loop re-drives it carrying what already
  failed. That is the self-healing half, and it is new.

WHY THE ROW IS BORN ``running`` AND NOT ``pending``. A pending row is claimable, and
the fast path is ALREADY producing this reply — the loop would claim it and answer
the same question a second time. Born running-with-a-lease, it becomes claimable
only when that lease expires, which is precisely the condition "the fast path did
not finish". The lease is the handover.

REUSE, NOT A SECOND ENGINE. Re-driving a recovered turn is the RetryActuator's
existing job — it already re-runs a floored turn's goal and delivers it. CLAUDE.md
forbids a second path that runs work; this module only creates and completes rows.

NOTHING HERE MAY COST A TURN. Every function is best-effort and logs its own
failure: a durable safety net that can drop the thing it protects is worse than no
net at all.
"""

from __future__ import annotations

import uuid
from typing import Any

from stackowl.infra.observability import log

#: How long the fast path is trusted to finish before the loop may take the turn
#: over. Generously longer than a slow real turn (p90 TTFT on this box is ~45s and
#: a multi-tool turn can run minutes), because reclaiming a turn that is merely
#: SLOW would answer the user twice.
TURN_LEASE_SECONDS = 900


def _destination(channel: object, chat_id: object = None) -> str:
    """Where this turn's answer must land, as ``channel[:address]``.

    A channel with no address (CLI, a single-terminal adapter) is still a real
    destination — it addresses its one terminal implicitly.

    EVERYTHING IS COERCED WITH str(), and that is not defensive padding: a Telegram
    chat id arrives as an INT. The first live run of this code failed on every turn
    with "'int' object has no attribute 'strip'" because the unit test passed the
    string "72055773" where the real adapter passes 72055773 — a test double that
    had stopped resembling the thing it stood in for, which is the defect shape
    this repo names explicitly. The types are asserted by a test below now.
    """
    ch = str(channel or "cli").strip() or "cli"
    addr = "" if chat_id is None else str(chat_id).strip()
    return f"{ch}:{addr}" if addr else ch


def loop_produces_replies(services: Any) -> bool:
    """Is the LOOP the primary producer for chat turns, or the fast path?

    Bakir's design has the loop find the answer and return it. That is off by
    default for a reason the config states in full: a loop-produced reply arrives
    in one piece when the work finishes, so the streaming/progress path is bypassed
    and a multi-tool turn on this hardware means minutes of silence.

    Degrades toward the FAST PATH on any doubt. Both modes are safe; but "nobody
    produces this turn" is not, and the fast path is the one that definitely
    answers.
    """
    try:
        cfg = getattr(services, "settings", None)
        if cfg is None:
            return False
        return bool(cfg.task_loop.produce_replies)
    except Exception as exc:
        log.tasks.warning(
            "[loop] could not read produce_replies — the fast path will answer",
            exc_info=exc,
        )
        return False


async def enqueue_turn_task(
    store: Any,
    *,
    trace_id: str,
    goal: str,
    channel: str | None,
    chat_id: object = None,
    session_key: str | None = None,
    owl_name: str | None = None,
    loop_produces: bool = False,
    loop: Any = None,
) -> None:
    """Record this turn as a durable task. Never raises.

    Keyed by ``trace_id``: it is already unique per turn, already threaded through
    every step, and already the key the response stream is registered under — so
    the completion seam can find the row without inventing a second identifier.

    ``loop_produces`` decides WHO answers, and the row's initial status is the whole
    mechanism. Exactly one producer must exist: a turn both run by the fast path and
    claimed by the loop is answered TWICE, and on Telegram the user sees two replies
    to one question.
    """
    if store is None:
        return
    try:
        from stackowl.pipeline.durable.task import DurableTask

        await store.enqueue(DurableTask(
            task_id=trace_id,
            goal=goal[:4000] or "(empty turn)",
            # pending  ⇒ claimable: the loop is expected to answer this.
            # running  ⇒ held: the fast path is already answering, and the loop
            #            only ever sees it if that lease EXPIRES, i.e. the fast
            #            path demonstrably did not finish.
            status="pending" if loop_produces else "running",
            trigger_kind="chat",
            destination=_destination(channel, chat_id),
            achievement="the reply is delivered to the user who asked",
            channel=channel,
            session_key=session_key,
            owl_name=owl_name,
            lease_owner=None if loop_produces else f"turn-{uuid.uuid4().hex[:8]}",
        ))
        if loop_produces and loop is not None:
            # Someone is WAITING on this one. A five-second tick is fine for a
            # sweep and awful for a person, so the enqueue wakes the loop now and
            # the tick stays the safety net. Best-effort: a failed wake costs one
            # tick of latency, never the row.
            try:
                loop.wake()
            except Exception as exc:
                log.tasks.warning(
                    "[loop] could not wake the loop — this turn waits for the next "
                    "tick instead",
                    exc_info=exc, extra={"_fields": {"trace_id": trace_id}},
                )
    except Exception as exc:
        # The reply must not depend on the task table being writable.
        log.tasks.error(
            "[loop] could not record this turn as a task — the turn proceeds, but "
            "it is NOT recoverable if it fails",
            exc_info=exc, extra={"_fields": {"trace_id": trace_id}},
        )


async def complete_turn_task(store: Any, *, trace_id: str, result: str) -> None:
    """Mark the turn delivered — the ONLY way a chat task completes. Never raises.

    An empty result deliberately does NOT complete it. Nothing reached the user, so
    nothing was achieved, and leaving the row open is what lets the loop recover the
    turn rather than record a success that never happened.
    """
    if store is None:
        return
    if not (result or "").strip():
        log.tasks.info(
            "[loop] turn produced no deliverable reply — leaving the task open so "
            "the loop can recover it",
            extra={"_fields": {"trace_id": trace_id}},
        )
        return
    try:
        await store.mark_delivered(trace_id, result=result[:8000])
    except Exception as exc:
        # The user HAS their answer; we simply could not record it. The lease will
        # expire and the loop will re-drive — which is why an effectful turn wants
        # an idempotency key.
        log.tasks.error(
            "[loop] could not mark a delivered turn complete — its lease will "
            "expire and the loop may re-drive it",
            exc_info=exc, extra={"_fields": {"trace_id": trace_id}},
        )
