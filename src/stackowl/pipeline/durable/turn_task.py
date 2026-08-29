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

    Keyed by ``trace_id``: already threaded through every step and already the key
    the response stream is registered under, so the completion seam can find the
    row without inventing a second identifier.

    IT IS NOT ALWAYS UNIQUE, and this docstring used to claim it was. A RETRY
    DERIVES its trace id from the turn it is retrying — `retry-eba4141b-fix`,
    `retry-eba4141b-fix-fix`, `<trace>-fix` — so a retry re-entering this path
    collides with the row its own first attempt wrote. Measured twice (2026-08-21
    and 2026-08-22) as `UNIQUE constraint failed: tasks.owner_id, tasks.task_id`.
    That collision is handled below rather than by minting a second identifier: the
    existing row IS this turn's row, which is exactly what recovery needs.

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
        # A UNIQUE COLLISION IS NOT A FAILURE, and reporting it as one was wrong in
        # both directions. This turn is already recorded — a retry re-entered with
        # the trace id its first attempt wrote — so the row exists, the loop can
        # claim it, and the turn IS recoverable. The old handler logged ERROR and
        # told the operator the exact opposite of the truth: "NOT recoverable if it
        # fails", about a turn whose row was sitting in the table.
        #
        # Distinguished by the constraint name rather than by catching
        # IntegrityError broadly: a UNIQUE violation on some OTHER column would be
        # a real defect and must keep surfacing at ERROR.
        if "UNIQUE constraint failed" in str(exc) and "task_id" in str(exc):
            log.tasks.info(
                "[loop] this turn is already recorded as a task — a retry reused "
                "its trace id, so the existing row stands and the turn stays "
                "recoverable",
                extra={"_fields": {"trace_id": trace_id}},
            )
            return
        # The reply must not depend on the task table being writable.
        log.tasks.error(
            "[loop] could not record this turn as a task — the turn proceeds, but "
            "it is NOT recoverable if it fails",
            exc_info=exc, extra={"_fields": {"trace_id": trace_id}},
        )


#: A turn whose effect was MEASURED ABSENT gets a small ceiling, not the ordinary
#: 30. The failure is already fed back, so it is either fixable in a few tries or it
#: needs Bakir — and every attempt can produce another message to him. Dead-lettering
#: quickly ESCALATES to him once, which is the honest trade: a few retries and one
#: escalation, never a silent grind.
UNACHIEVED_EFFECT_MAX_ATTEMPTS = 3

#: The failure class recorded when a turn delivered an apology instead of the work.
UNACHIEVED_EFFECT_CLASS = "unachieved_effect"


def unachieved_effect_of(state: Any) -> str | None:
    """The effect this turn PROMISED and whose absence its own verify() observed.

    ``effects_measured_absent`` is deliberately the strict subset that was measured,
    not everything unverified: re-driving a measured-absent effect cannot double a
    side effect because nothing landed, while an UNKNOWN outcome is left alone so
    the burden of proof stays on the claim.

    Never raises — bookkeeping must not cost a delivered turn.
    """
    try:
        absent = tuple(getattr(state, "effects_measured_absent", None) or ())
    except Exception:  # pragma: no cover — a hostile state must not break delivery
        return None
    return str(absent[0]) if absent else None


#: A turn that was BLOCKED gets the same small ceiling as one whose effect was
#: measured absent, and for the same reason: the block is fed back, so it is either
#: routable in a couple of tries or it needs Bakir to grant something.
BLOCKED_CAPABILITY_CLASS = "blocked_capability"


def blocked_capability_of(state: Any) -> str | None:
    """A capability this turn was refused, or None.

    Never raises — bookkeeping must not cost a delivered turn.
    """
    try:
        denied = tuple(getattr(state, "capabilities_denied", None) or ())
    except Exception:  # pragma: no cover — a hostile state must not break delivery
        return None
    return str(denied[0]) if denied else None


#: A floored turn delivered an HONEST APOLOGY instead of the work. Same small
#: ceiling as the other two unachieved classes, and for the same reason: the floor
#: reason is fed back, so it is either routable in a couple of tries or it needs
#: Bakir.
FLOORED_TURN_CLASS = "floored_turn"


def floored_turn_of(state: Any) -> str | None:
    """The floor this turn delivered instead of the work, or None.

    THE THIRD UNACHIEVED CASE, and the one whose absence cost the operator a
    duplicate reply. ``unachieved_effect_of`` reads ``effects_measured_absent`` and
    ``blocked_capability_of`` reads ``capabilities_denied``; an OVERCLAIM floor sets
    neither, so a floored turn used to read as ACHIEVED here and was closed as
    delivered — while ``persist_turn`` had separately minted a second, claimable row
    to retry the very same question. One turn, two producers, two contradictory
    verdicts, both obeyed (measured on trace e6c1d3e1, 2026-08-29).

    Asked of the RESPONSES, which is where the floor marker lives and where
    ``turn_persist._turn_floored`` reads its first signal — one source, not a second
    copy of the rule.

    Never raises — bookkeeping must not cost a delivered turn.
    """
    try:
        for chunk in getattr(state, "responses", None) or ():
            if getattr(chunk, "is_floor", False):
                return "floored"
    except Exception:  # pragma: no cover — a hostile state must not break delivery
        return None
    return None


async def complete_turn_task(
    store: Any, *, trace_id: str, result: str, state: Any = None,
) -> str:
    """Mark the turn delivered — the ONLY way a chat task completes. Never raises.

    An empty result deliberately does NOT complete it. Nothing reached the user, so
    nothing was achieved, and leaving the row open is what lets the loop recover the
    turn rather than record a success that never happened.

    NOR DOES AN APOLOGY. Bakir, 2026-08-19: "if I'm asking to do something, he
    does." Measured over every log the platform has written: 134 overclaims
    detected, 51 where only the WORDING was corrected, and exactly 1 where the work
    was actually redone — 132 detected and then abandoned, 16 of them owl_build. The
    delivery gate makes one in-turn corrective attempt; when it fails it floors the
    answer to an honest "I couldn't complete this" and the turn completed as
    DELIVERED, because the reply had reached him.

    For a QUESTION the outcome is the answer, and delivery is achievement. For an
    EFFECTFUL request the outcome is the effect, and an apology about the effect is
    not the effect. So a turn whose own verification measured the promised effect
    absent goes back on the loop carrying what failed — the loop's stated contract,
    applied to the case it was skipping.
    """
    if store is None:
        return "no_store"
    unachieved = unachieved_effect_of(state)
    # PRIORITY ORDER, stated because three conditions can fire on one event: a
    # measured-absent effect is the most specific and wins; a blocked capability
    # next; a bare floor last, because it is the least informative of the three.
    floored = None if unachieved else floored_turn_of(state)
    blocked = None if unachieved else blocked_capability_of(state)
    if floored and not blocked:
        # A FLOOR IS AN UNACHIEVED GOAL. It reaches the user immediately — we do
        # NOT suppress it, because 75% of floors are provider outages a retry
        # cannot beat, and silence is worse than an honest apology. What changes is
        # that the follow-up belongs to THIS row: the existing requeue, the existing
        # ceiling, the existing dead-letter escalation. Nothing new runs the work,
        # and no second row can be claimed while this one is still leased.
        try:
            status = await store.fail_and_requeue(
                trace_id,
                error=(
                    "the turn delivered an honest floor instead of the work — the "
                    "user was told it could not be completed. Try again with what "
                    "the floor named as failing, or say precisely what is blocking "
                    "it."
                ),
                failure_class=FLOORED_TURN_CLASS,
            )
            log.tasks.info(
                "[loop] the turn FLOORED — returning its own row to the loop "
                "instead of closing it as done",
                extra={"_fields": {"trace_id": trace_id, "outcome": str(status)}},
            )
            # THE ANSWER DELIVER NEEDS. "requeued" means the loop still has
            # attempts, so the floor may be HELD; anything else means the loop has
            # stopped trying and the floor must reach the user NOW. Reporting the
            # status rather than a bare bool keeps the caller honest about which
            # of the two happened.
            return "requeued" if str(status) == "pending" else str(status or "failed")
        except Exception as exc:
            log.tasks.error(
                "[loop] could not return a floored turn to the loop",
                exc_info=exc, extra={"_fields": {"trace_id": trace_id}},
            )
            # A requeue we could not perform is NOT ownership. Falling back to a
            # non-holding outcome means the user gets the floor — the safe side.
            return "requeue_failed"
    if blocked:
        # BEING BLOCKED IS A FAILURE THE LOOP MUST SEE. The user asked for something
        # and it did not happen — the same unachieved goal as a measured-absent
        # effect, arriving one step earlier. Nothing new runs the work: the existing
        # requeue, the existing ceiling, and the existing dead-letter escalation,
        # which is what finally tells Bakir "I need this capability" instead of
        # closing the task as done.
        try:
            await store.fail_and_requeue(
                trace_id,
                error=(
                    f"the turn was BLOCKED: `{blocked}` is not permitted for this "
                    f"owl, so the work was never attempted. Either route around it "
                    f"(delegate_task to an owl that holds it), or ask the user to "
                    f"grant it — owl_build action='grant' with explicit_tools="
                    f"['{blocked}'] — and say plainly that you need it."
                ),
                failure_class=BLOCKED_CAPABILITY_CLASS,
            )
            log.tasks.info(
                "[loop] the turn was blocked on a capability — returning it to the "
                "loop instead of closing it as done",
                extra={"_fields": {"trace_id": trace_id, "blocked": blocked}},
            )
        except Exception as exc:
            log.tasks.error(
                "[loop] could not return a blocked turn to the loop",
                exc_info=exc, extra={"_fields": {"trace_id": trace_id}},
            )
        return "blocked_or_unachieved"
    if unachieved:
        try:
            await store.fail_and_requeue(
                trace_id,
                error=(
                    f"the turn told the user it had done this, but `{unachieved}` "
                    f"was verified and its effect is NOT present — nothing was "
                    f"actually changed. Do it for real, or say precisely what is "
                    f"blocking it (if it needs a capability this owl lacks, ask the "
                    f"user to grant it with owl_build action='grant')."
                ),
                failure_class=UNACHIEVED_EFFECT_CLASS,
            )
            log.tasks.info(
                "[loop] the reply was delivered but the WORK was not — returning "
                "the turn to the loop",
                extra={"_fields": {
                    "trace_id": trace_id, "unachieved": unachieved,
                }},
            )
        except Exception as exc:
            # The user has their (honest) reply. Failing to requeue must not also
            # cost the turn — the lease will expire and recovery will pick it up.
            log.tasks.error(
                "[loop] could not return an unachieved turn to the loop",
                exc_info=exc, extra={"_fields": {"trace_id": trace_id}},
            )
        return "blocked_or_unachieved"
    if not (result or "").strip():
        log.tasks.info(
            "[loop] turn produced no deliverable reply — leaving the task open so "
            "the loop can recover it",
            extra={"_fields": {"trace_id": trace_id}},
        )
        return "no_reply"
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
        return "mark_failed"
    return "completed"
