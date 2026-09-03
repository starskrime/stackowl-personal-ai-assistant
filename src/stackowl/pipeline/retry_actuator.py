"""RetryActuator — re-runs a floored turn's goal, steered away from the
capability that already failed.

Reuses the exact scheduled-turn pattern goal_execution.py already uses
(PipelineState construction + backend.run()) rather than inventing a second
way to inject a synthetic turn. Shared by the cron sweep (retry_sweep.py,
Task 6) and the manual "do it again" path (Task 7) — one function, one place
the retry semantics live.

ponytail: capability avoidance is PROMPT-STEERED (the re-run's goal text
names the banned capabilities and asks the model not to use them again), not
a hard filter threaded through tool-selection. The model can still pick a
banned capability if it insists. Upgrade path: thread banned_capabilities
into execute.py's tool-selection as a real exclusion list if soft steering
proves unreliable in practice.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, cast

from telegram.error import RetryAfter

from stackowl.infra import retry_ledger
from stackowl.infra.observability import log
from stackowl.notifications.deliverer import _TargetedSender
from stackowl.pipeline.delivery_gate import (
    describe_attempt_evidence,
    failed_capabilities_for_state,
)
from stackowl.pipeline.retry_attempt import RetryAttempt
from stackowl.pipeline.state import PipelineState

if TYPE_CHECKING:  # pragma: no cover — typing only
    from stackowl.channels.registry import ChannelRegistry
    from stackowl.pipeline.backends.base import OrchestratorBackend

# Fixed cadence kept for any delivery failure that ISN'T a Telegram flood-control
# error (matches the sweep's own 1-minute tick — see retry_sweep.py).
_DEFAULT_DELIVERY_RETRY_DELAY_SECONDS = 60.0
# Small margin past what Telegram itself reports, so a retry landing exactly at
# the boundary doesn't get flood-controlled again by clock skew.
_DELIVERY_RETRY_DELAY_BUFFER_SECONDS = 5.0


#: How much of `last_error` the retry prompt carries. `last_error` holds up to
#: 2000 chars; pasting all of it in front of a short ask would drown the thing
#: being asked for — the unbounded-prose failure that `banned_capabilities` was
#: chosen to avoid in the first place.
_RETRY_REASON_CHARS = 400


def _delivery_retry_delay_seconds(exc: BaseException) -> float:
    """Honor Telegram's own flood-control cooldown when the delivery failure is
    a ``RetryAfter`` — blindly retrying on the fixed cadence while still banned
    only extends the ban. Any other delivery failure keeps the prior cadence.
    """
    if isinstance(exc, RetryAfter):
        retry_after = exc.retry_after
        seconds = (
            retry_after.total_seconds()
            if isinstance(retry_after, timedelta)
            else float(retry_after)
        )
        return seconds + _DELIVERY_RETRY_DELAY_BUFFER_SECONDS
    return _DEFAULT_DELIVERY_RETRY_DELAY_SECONDS


@dataclass(frozen=True, slots=True)
class RetryOutcome:
    status: str  # "completed" | "pending" | "failed"
    #: Capabilities this attempt proved dead, for the NEXT attempt to avoid.
    #: Bakir's contract — "next loop when it picks it, it also looks: is any
    #: previous one? Yes — learn from that experience." It used to be computed by
    #: `_pick_newly_failed`, handed to a table the ONE-loop migration stopped
    #: writing, and lost. Task 8b7c4029 then failed IDENTICALLY 74 times over
    #: 14h33m because every attempt re-tread what the last one had already burned.
    banned: tuple[str, ...] = ()
    #: WHAT THE WORK HIT, in the turn's own evidence — not what the machinery
    #: did. Without this the durable loop re-describes every failure as "retry
    #: did not deliver (actuator reported 'pending')" and stores THAT as the
    #: task's last_error, which is what the next attempt is then shown as "what
    #: happened last time". A tautology cannot change a strategy.
    reason: str = ''


def _native_chat_id(raw: str) -> str | int:
    """Return a chat id in the type its channel actually uses.

    Telegram's ids are numeric and its API wants an int; slack's ("C123ABC") and
    whatsapp's ("+15551234") are not numbers at all. Coercing everything to int
    raised on slack and — worse — silently rewrote a whatsapp address to a
    different number. Only a purely-numeric id becomes an int; everything else is
    passed through untouched.
    """
    text = str(raw).strip()
    return int(text) if text.isdigit() else text


class RetryActuator:
    """Shared retry function — called by both the cron sweep and manual retry."""

    def __init__(
        self,
        *,
        backend: OrchestratorBackend,
        channel_registry: ChannelRegistry,
    ) -> None:
        # NO STORE. This used to take a RetryQueueStore and write three
        # bookkeeping calls into `retry_queue` — a table whose only writer was
        # removed on 2026-08-28 (commit 49601f50, "a floored turn retries on the
        # ONE loop"). Every row this actuator now sees is a SYNTHETIC one built
        # by task_loop_runner from a `tasks` row, so none of those writes could
        # ever match: MEASURED across three days of logs, 14 ERROR lines from
        # mark_attempt_failed, 11 "no matching row — wrong id" warnings from
        # mark_completed, and 47 "no matching pending row" from reschedule.
        # The bookkeeping that IS real lives on `tasks` and is done by the runner
        # from this method's return value.
        self._backend = backend
        self._channel_registry = channel_registry

    async def run_corrective(
        self, *, original: PipelineState, correction: str
    ) -> PipelineState | None:
        """One bounded in-turn corrective re-run of a gate-rejected draft.

        The agentic upgrade the delivery gates lacked: instead of replacing a
        rejected draft with an honest floor and stopping, feed the REJECTION
        REASON back to the model and run the full pipeline once more (tools
        included — a "you never actually looked this up" correction needs a
        real ``web_search``, not a re-wording). Mirrors ``attempt_retry``'s
        synthetic-replay construction: ``defer_delivery=True`` (the parent
        turn owns delivery), ``retry_replay=True`` (no retry-queue row
        minting), plus ``corrective_replay=True`` so the child's OWN delivery
        gates never spawn a grandchild (strict one-round bound). The child
        runs the same gate cascade internally, so a returned state is one
        whose corrected draft already CLEARED the gates that rejected the
        parent's — the platform never lowers the bar, it re-asks the model to
        meet it.

        Returns the child's final state on a gate-clean corrected answer, or
        ``None`` (caller keeps its existing floor) when the child floored,
        produced nothing, or raised. Never raises.
        """
        log.engine.info(
            "retry_actuator.run_corrective: entry",
            extra={"_fields": {
                "trace_id": original.trace_id,
                "correction": correction[:120],
            }},
        )
        state = PipelineState(
            trace_id=f"{original.trace_id}-fix",
            session_key=original.session_key,
            # ESC-59 — a correction is the SAME conversation as the turn it
            # corrects, so it inherits the incarnation rather than minting one.
            # assemble only reads the frozen prompt when this is set
            # (assemble.py:133); a fresh id here would cold-build on the very lane
            # most likely to still hold a warm one.
            conversation_id=original.conversation_id,
            input_text=(
                f"{original.input_text}\n\n"
                f"[Your previous draft was rejected before delivery: {correction} "
                "Produce a corrected answer now — use your tools where needed.]"
            ),
            channel=original.channel,
            owl_name=original.owl_name,
            pipeline_step="",
            interactive=False,
            # THIS TEXT IS OURS, NOT THE USER'S. The correction above is composed
            # here, and this state keeps the ORIGINAL human session key — so
            # `is_machine_lane` cannot see it (a prefix check on
            # "goal-"/"incident-", and this is a Telegram lane). Without the flag
            # the augmented prompt is filed as a durable user utterance.
            # MEASURED 2026-08-31: 145 of 368 staged_facts (39%) carry retry/RCA
            # markers reading 'User: (Retry attempt 2. What happened last
            # time...' — almost exactly the 37.1% of diagnostics that migration
            # 0112 deleted 107,576 rows to be rid of.
            input_is_synthetic=True,
            defer_delivery=True,
            retry_replay=True,
            corrective_replay=True,
            # Workstream B — the corrective child's trace_id is already a
            # derivative of the parent's (f"{original.trace_id}-fix"), but
            # the LINEAGE id must match the PARENT's trace_id verbatim so a
            # corrective re-run correlates with the turn it's correcting in
            # the retry ledger, not with itself.
            retry_lineage_id=original.trace_id,
        )
        try:
            final_state = await self._backend.run(state)
        except Exception as exc:  # never raise into the delivery gate
            log.engine.error(
                "retry_actuator.run_corrective: corrective pipeline raised — keeping floor",
                exc_info=exc, extra={"_fields": {"trace_id": original.trace_id}},
            )
            return None
        floored = (
            not final_state.responses
            or any(c.is_floor for c in final_state.responses)
            or final_state.overclaim_blocked
            or final_state.budget_capped
        )
        log.engine.info(
            "retry_actuator.run_corrective: exit",
            extra={"_fields": {
                "trace_id": original.trace_id, "corrected": not floored,
            }},
        )
        return None if floored else final_state

    async def attempt_retry(self, row: RetryAttempt) -> RetryOutcome:
        # 1. ENTRY
        log.scheduler.info(
            "retry_actuator.attempt_retry: entry",
            extra={"_fields": {
                "retry_id": row.id, "attempt_count": row.attempt_count,
                "banned_capabilities": row.banned_capabilities,
            }},
        )
        augmented_goal = self._augment_goal(row)
        trace_id = f"retry-{uuid.uuid4().hex[:8]}"
        state = PipelineState(
            trace_id=trace_id,
            session_key=row.session_key,
            # ESC-59 — this retry attempt IS the incarnation. Each attempt is a
            # distinct run of the lane (the trace_id is already minted fresh per
            # attempt, and a test pins that), so reusing it as the stamp keeps a
            # multi-step attempt on one frozen prompt without letting two attempts
            # share one.
            conversation_id=trace_id,
            input_text=augmented_goal,
            channel=row.channel,
            # RESUME WITH THE OWL THAT RAN IT, and PIN it. This was hardcoded
            # to "secretary" — which is `_FALLBACK_OWL`, so triage read it as
            # "nobody chose an owl" and routed the goal afresh. MEASURED
            # 2026-09-03: a turn routed to secretary built the `jobmarket` owl,
            # floored after the work was done, and its recovery was routed to
            # jobmarket — the owl the turn had just created. It answered "that
            # agent already exists — I'm it" to the person who had asked for it
            # to be built, because it had no idea the turn had built it.
            #
            # A RETRY IS A RESUMPTION, NOT A NEW QUESTION. The owl is a decision
            # already made, so it is pinned; re-deciding it lets the world the
            # turn CHANGED pick who explains the change.
            owl_name=row.owl_name or "secretary",
            owl_pinned=bool(row.owl_name),
            pipeline_step="",
            interactive=False,
            # Same reason as the correction path above: `augmented_goal` is
            # composed by _augment_goal, not typed by anyone, and this state
            # reuses the row's human session key.
            input_is_synthetic=True,
            defer_delivery=True,
            # This run IS the retry — its own outcome is tracked below via
            # mark_attempt_failed(). Without this flag, a floor on THIS
            # attempt would make persist_turn mint a SECOND, independent
            # retry_queue row (attempt_count=0, due immediately) instead of
            # feeding back into this row's own attempt history.
            retry_replay=True,
            # Workstream B — row.id is STABLE across every attempt of this
            # same goal, unlike trace_id (freshly minted above on every
            # attempt_retry call). This is what lets the retry ledger
            # correlate attempt N's log lines with attempt N+1's despite
            # each having its own trace_id.
            retry_lineage_id=row.id,
            # The ban is now ENFORCED, not just narrated in _augment_goal above:
            # execute excludes these from the presented tool set, so a model that
            # already failed with a capability cannot simply insist on it. See
            # this module's docstring — it named this the upgrade path, gated on
            # soft steering proving unreliable, and 27 retries against 3
            # substitutions over 7 days is that proof.
            banned_capabilities=tuple(row.banned_capabilities or ()),
        )
        try:
            # 3. STEP — drive the pipeline exactly like a scheduled goal.
            final_state = await self._backend.run(state)
        except Exception as exc:  # never raise into the scheduler loop
            log.scheduler.error(
                "retry_actuator.attempt_retry: pipeline raised",
                exc_info=exc, extra={"_fields": {"retry_id": row.id}},
            )
            outcome = await self._handle_failure(row, str(exc), newly_failed_capability="")
            log.scheduler.info(
                "retry_actuator.attempt_retry: exit",
                extra={"_fields": {"retry_id": row.id, "status": outcome.status}},
            )
            return outcome

        # 2. DECISION — floored (still couldn't) vs a genuine answer. A budget-capped
        # final state (delivery_gate.py's own established signal — see
        # is_consequential_giveup_now / surface_persistence_handoff) is treated the
        # same as an explicit floor chunk: the turn was cut off mid-thought, not
        # genuinely completed, even when the partial text never got an is_floor chunk
        # (execute.py's default-backstop budget-breach branch omits it).
        # overclaim_blocked mirrors run_corrective's own floor check above — an
        # overclaim-gated draft already carries is_floor on its replacement chunk
        # in the normal case, but checking the flag directly (not just the chunk)
        # keeps this in lockstep with run_corrective instead of two gates silently
        # drifting apart on what "floored" means for a re-run turn.
        floored = (
            any(c.is_floor for c in final_state.responses)
            or final_state.budget_capped
            or final_state.overclaim_blocked
        )
        if floored:
            newly_failed = self._pick_newly_failed(row, final_state)
            # "retry attempt still floored" is a TAUTOLOGY — it says the retry
            # failed, which the loop already knows, and it is what `_augment_goal`
            # then shows the NEXT attempt as "what happened last time". That is
            # why retries 2-6 of one task re-entered an identical web_fetch-first
            # path five times over. The turn's own evidence is in hand right here;
            # carry THAT instead, and fall back to the old wording only when the
            # attempt touched nothing to describe.
            reason = describe_attempt_evidence(final_state) or "retry attempt still floored"
            log.scheduler.info(
                "retry_actuator.attempt_retry: carrying the attempt's evidence "
                "forward",
                extra={"_fields": {"retry_id": row.id, "attempt": row.attempt_count,
                                   "reason": reason[:200],
                                   "newly_failed": newly_failed}},
            )
            outcome = await self._handle_failure(
                row, reason, newly_failed_capability=newly_failed,
            )
            # 4. EXIT
            log.scheduler.info(
                "retry_actuator.attempt_retry: exit",
                extra={"_fields": {"retry_id": row.id, "status": outcome.status}},
            )
            return outcome

        # "".join, not "\n".join: a streamed response is one chunk per token
        # (execute.py yields once per delta) — joining with a newline put every
        # token on its own line in the delivered message. Matches deliver.py's
        # normal-path join (`combined = "".join(...)`), which never had this bug.
        answer_text = "".join(c.content for c in final_state.responses if c.content).strip()
        # DELIVERY AND BOOKKEEPING ARE SEPARATE FACTS. They used to share one
        # `try`, so a send that SUCCEEDED followed by a mark_completed that failed
        # reported "pending" — and the caller treats "pending" as "nothing reached
        # the user" and re-drives. MEASURED 2026-08-19: Bakir's task 43be4591 sent
        # him the same recovered answer at 01:18:46, 01:29:38 and 01:34:34, once
        # per attempt, and had 27 attempts left to keep going.
        #
        # Bakir's rule decides which fact wins: "a task is complete when its
        # outcome reached its DESTINATION". Once the answer has landed the
        # achievement is met, whatever the bookkeeping did afterwards. Re-sending
        # is not a smaller error than failing to record.
        try:
            # 3. STEP — deliver. Recording completion is the CALLER's job: the
            # runner marks the `tasks` row from this method's return value, and
            # that is the only bookkeeping that exists now.
            await self._deliver_success(row, answer_text)
        except Exception as exc:  # never raise into the scheduler loop
            delay_seconds = _delivery_retry_delay_seconds(exc)
            log.scheduler.error(
                "retry_actuator.attempt_retry: success-path delivery/store failed",
                exc_info=exc, extra={"_fields": {"retry_id": row.id, "delay_seconds": delay_seconds}},
            )
            # The reschedule that used to happen here wrote `next_retry_at` on a
            # retry_queue row, and it paced the deleted 1-minute sweep. Both are
            # gone; the ONE loop owns pacing for a `tasks` row. `delay_seconds`
            # is kept because it is the reported reason, not a timer.
            #
            # "pending" means the answer was produced but not delivered, so the
            # loop must try again rather than mark it done — the same contract as
            # before, now reported rather than written.
            outcome = RetryOutcome(status="pending")
            log.scheduler.info(
                "retry_actuator.attempt_retry: exit",
                extra={"_fields": {"retry_id": row.id, "status": outcome.status}},
            )
            return outcome
        log.scheduler.info(
            "retry_actuator.attempt_retry: exit",
            extra={"_fields": {"retry_id": row.id, "status": "completed"}},
        )
        return RetryOutcome(status="completed")

    def _augment_goal(self, row: RetryAttempt) -> str:
        """Tell the retry WHAT burned and WHY, so it is constrained, not blind.

        MEASURED 2026-08-20 on task 86de5841. The loop saw the turn was blocked on
        `shell`, requeued it with the reason AND the remedy stored on the row, then
        claimed it and re-drove it for eight and a half minutes — and the retry
        reached for `owl_build action='edit'`, which by design can never widen
        authority, instead of `action='grant'`, which exists to do exactly that.
        `grant` was called zero times.

        The cause was here: this method built the prompt from
        `banned_capabilities` ALONE and never read `last_error`, so the explanation
        `fail_and_requeue` had carefully written to the row was never shown to the
        model. Bakir's rule — "adding previous failure or action details... so next
        loop when it picks it, it also looks: is any previous one? Yes — learn from
        that experience" — was half kept: WHICH capability burned was passed on, WHY
        and WHAT TO DO INSTEAD were dropped.

        A blocked turn bans NOTHING (the tool never ran), so under the old code its
        goal went back verbatim with no hint at all — the worst case of the three.
        """
        banned = ", ".join(row.banned_capabilities)
        reason = (row.last_error or "").strip()
        if not banned and not reason:
            return row.goal
        parts = [f"(Retry attempt {row.attempt_count + 1}."]
        if banned:
            parts.append(
                f" A previous attempt at this same ask already failed using "
                f"{banned} — try a genuinely different approach or tool this time, "
                f"do not repeat the same failed path."
            )
        if reason:
            parts.append(f" What happened last time: {reason[:_RETRY_REASON_CHARS]}")
        parts.append(")\n\n")
        return "".join(parts) + row.goal

    def _pick_newly_failed(self, row: RetryAttempt, final_state: PipelineState) -> str:
        """Name the capability that actually FAILED and is not already banned.

        A BAN IS NOT ADVICE. The returned name is removed from the presented tool
        array for the next attempt (``execute._restrict_to_for_turn``) — the
        capability becomes genuinely unavailable, not merely discouraged. So the
        only acceptable source is the turn's record of what FAILED.

        WHAT FAILED, not what was touched. The original positional guess banned
        ``send_message`` on a turn whose tool_sequence was
        ``["search_files","search_files","read_file"]``, steering the next attempt
        away from something innocent while the real dead end stayed open.

        THE FALLBACK IS DELETED, NOT GUARDED. That fix left a second rung reaching
        for ``_attempts_for_state``, which unions ``consequential_failures`` with
        ``consequential_successes`` — so it could ban a capability that WORKED. It
        was harmless only by accident (both sources happen to be write-filtered
        today) and would have become the primary path the moment either was
        widened, which is exactly what the sibling change to
        ``describe_attempt_evidence`` does to the evidence sentence. Measured
        baseline across every log file: 15 ban events, every one write or
        consequential severity, zero read tools. Banning ``web_fetch`` on a
        research task blinds it of the tool the task exists to use, and nothing
        raises when it happens — the turn just quietly has fewer tools.

        Returns "" when nothing new failed (e.g. a read-only sweep that hit a
        budget ceiling) — the store treats an empty string as "nothing to add".
        That turn is not silently unlearned: its evidence rides the prose channel
        via ``describe_attempt_evidence``, which names the ceiling and the repeat
        run instead of removing a tool.
        """
        for name in failed_capabilities_for_state(final_state):
            if name not in row.banned_capabilities:
                return name
        return ""

    async def _deliver_success(self, row: RetryAttempt, answer_text: str) -> None:
        adapter = self._channel_registry.get(row.channel)
        if row.channel_chat_id and row.channel_message_id and hasattr(adapter, "edit_message"):
            try:
                await adapter.edit_message(
                    int(row.channel_chat_id), int(row.channel_message_id), answer_text,
                )
                return
            except Exception as exc:  # edit can fail (message too old/deleted) — fall back
                log.telegram.error(
                    "retry_actuator._deliver_success: edit failed — sending new message",
                    exc_info=exc, extra={"_fields": {"retry_id": row.id}},
                )
        # notifications/deliverer.py's own convention (_TargetedSender): an
        # explicit chat_id is only ever meaningful for a chat-addressable
        # (telegram) channel — retry_queue rows are telegram-only today per
        # insert_pending's default (see turn_persist.py). Reusing the same
        # Protocol cast here instead of a second ad-hoc dispatch.
        if row.channel_chat_id:
            # NOT int(). The chat id is CHANNEL-NATIVE: telegram's is numeric, but
            # slack's is "C123ABC" (int() raises) and whatsapp's is "+15551234"
            # (int() SILENTLY yields 15551234 — a different address, which is worse
            # than raising). _TargetedSender.send_text already accepts str | int, so
            # the value is passed through in the channel's own type and each adapter
            # coerces what it actually needs.
            #
            # This was invisible while retry_queue rows were telegram-only. It stops
            # being invisible the moment the loop produces replies for every
            # gateway, which is what made it worth finding.
            await cast("_TargetedSender", adapter).send_text(
                answer_text, chat_id=_native_chat_id(row.channel_chat_id)
            )
        else:
            await adapter.send_text(answer_text)

    async def _handle_failure(
        self, row: RetryAttempt, error: str, *, newly_failed_capability: str,
    ) -> RetryOutcome:
        # THE STORE ROUND-TRIP IS GONE, AND ITS EXCEPT BRANCH WAS ALREADY THE
        # IMPLEMENTATION. mark_attempt_failed raised on every call — the row is
        # synthetic and retry_queue has had no writer since 2026-08-28 — so this
        # method's real behaviour was the handler for a failure that was
        # guaranteed. A call kept for its exception is not a call; it was 14
        # ERROR lines in three days about a by-design condition, which is how an
        # error log stops meaning anything.
        attempt_number = int(getattr(row, "attempt_count", 0) or 0) + 1

        # Workstream B — the attempt number a later reader (task_outcomes) needs
        # to tell "attempt 1 of many" from "attempt 40 and still failing".
        # provider=row.id matches this row's retry_lineage_id, so both are the
        # same key. NOTE: attempt_retry runs outside any backend-bound
        # retry_ledger scope (backend.run() binds/resets its OWN per-turn context
        # and returns before _handle_failure is ever called) — this call is a
        # graceful no-op until a caller binds a ledger scope spanning the
        # goal-retry lifecycle.
        retry_ledger.record_retry(
            kind="goal_retry_attempt", provider=row.id,
            detail=error[:120], attempt_number=attempt_number,
        )
        # ALWAYS "pending", never a terminal "failed" (owner decision
        # 2026-07-22): a goal re-arms and is never abandoned, so there is no
        # give-up notification to send here. THE LEARNING TRAVELS WITH IT — the
        # capability that just failed is returned so the next attempt is
        # constrained rather than blind, which is what the ONE loop reads off
        # this outcome and writes onto the `tasks` row.
        return RetryOutcome(
            status="pending",
            banned=(newly_failed_capability,) if newly_failed_capability else (),
            reason=error,
        )
