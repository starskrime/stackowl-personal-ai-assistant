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

from stackowl.infra.observability import log
from stackowl.memory.retry_queue_store import RetryQueueRow, RetryQueueStore
from stackowl.notifications.deliverer import _TargetedSender
from stackowl.pipeline.delivery_gate import _attempts_for_state
from stackowl.pipeline.state import PipelineState

if TYPE_CHECKING:  # pragma: no cover — typing only
    from stackowl.channels.registry import ChannelRegistry
    from stackowl.pipeline.backends.base import OrchestratorBackend

_STILL_FAILED_NOTICE = (
    "Still couldn't complete this after {attempts} tries: {goal}"
)

# Fixed cadence kept for any delivery failure that ISN'T a Telegram flood-control
# error (matches the sweep's own 1-minute tick — see retry_sweep.py).
_DEFAULT_DELIVERY_RETRY_DELAY_SECONDS = 60.0
# Small margin past what Telegram itself reports, so a retry landing exactly at
# the boundary doesn't get flood-controlled again by clock skew.
_DELIVERY_RETRY_DELAY_BUFFER_SECONDS = 5.0


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


class RetryActuator:
    """Shared retry function — called by both the cron sweep and manual retry."""

    def __init__(
        self,
        *,
        backend: OrchestratorBackend,
        channel_registry: ChannelRegistry,
        retry_store: RetryQueueStore,
    ) -> None:
        self._backend = backend
        self._channel_registry = channel_registry
        self._retry_store = retry_store

    async def attempt_retry(self, row: RetryQueueRow) -> RetryOutcome:
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
            session_id=row.session_id,
            input_text=augmented_goal,
            channel=row.channel,
            owl_name="secretary",
            pipeline_step="",
            interactive=False,
            defer_delivery=True,
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
        floored = any(c.is_floor for c in final_state.responses) or final_state.budget_capped
        if floored:
            newly_failed = self._pick_newly_failed(row, final_state)
            outcome = await self._handle_failure(
                row, "retry attempt still floored", newly_failed_capability=newly_failed,
            )
            # 4. EXIT
            log.scheduler.info(
                "retry_actuator.attempt_retry: exit",
                extra={"_fields": {"retry_id": row.id, "status": outcome.status}},
            )
            return outcome

        answer_text = "\n".join(c.content for c in final_state.responses if c.content).strip()
        try:
            # 3. STEP — deliver + record completion; mirrors deliverer.py's
            # _transport contract: this must never raise into the caller.
            await self._deliver_success(row, answer_text)
            await self._retry_store.mark_completed(row.id)
        except Exception as exc:  # never raise into the scheduler loop
            delay_seconds = _delivery_retry_delay_seconds(exc)
            log.scheduler.error(
                "retry_actuator.attempt_retry: success-path delivery/store failed",
                exc_info=exc, extra={"_fields": {"retry_id": row.id, "delay_seconds": delay_seconds}},
            )
            # Without this, the row's next_retry_at is unchanged (still due),
            # so the NEXT 1-minute sweep tick retries immediately — hammering
            # an already flood-controlled channel and extending the ban
            # instead of waiting it out. Reschedule failure is best-effort:
            # worst case is the old behavior (immediate re-try), never a
            # crash into the scheduler loop.
            try:
                await self._retry_store.reschedule(
                    row.id, delay_seconds=delay_seconds, error=str(exc),
                )
            except Exception as store_exc:
                log.scheduler.error(
                    "retry_actuator.attempt_retry: reschedule after delivery "
                    "failure also failed",
                    exc_info=store_exc, extra={"_fields": {"retry_id": row.id}},
                )
            # The row's DB status is unchanged by a failed delivery/mark_completed
            # (still "pending"), so reporting "pending" here matches DB truth and
            # lets a future sweep retry rather than silently losing the answer.
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

    def _augment_goal(self, row: RetryQueueRow) -> str:
        if not row.banned_capabilities:
            return row.goal
        banned = ", ".join(row.banned_capabilities)
        return (
            f"(Retry attempt {row.attempt_count + 1}: a previous attempt at this "
            f"same ask already failed using {banned} — try a genuinely different "
            f"approach or tool this time, do not repeat the same failed path.)\n\n"
            f"{row.goal}"
        )

    def _pick_newly_failed(self, row: RetryQueueRow, final_state: PipelineState) -> str:
        """Name the FIRST capability this retry attempt touched that wasn't
        already banned — the real "newly failed" signal for
        ``mark_attempt_failed``. Reuses the same tool-attempt lookup the
        original floor used (``_attempts_for_state``, shared with
        ``turn_persist.py``'s own ``insert_pending`` call) instead of a second
        way to enumerate what a turn tried. Returns "" when nothing new was
        attempted (e.g. the retry floored before touching any tool) — the
        store treats an empty string as "nothing to add" (no bogus re-ban of
        an already-banned capability).
        """
        for name in _attempts_for_state(final_state):
            if name not in row.banned_capabilities:
                return name
        return ""

    async def _deliver_success(self, row: RetryQueueRow, answer_text: str) -> None:
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
            await cast("_TargetedSender", adapter).send_text(
                answer_text, chat_id=int(row.channel_chat_id)
            )
        else:
            await adapter.send_text(answer_text)

    async def _handle_failure(
        self, row: RetryQueueRow, error: str, *, newly_failed_capability: str,
    ) -> RetryOutcome:
        try:
            # 3. STEP — mark_attempt_failed raises ValueError when the row was
            # raced (already moved off "pending" by a concurrent sweep/manual-
            # retry call) or re-raises on transaction failure — this must
            # never raise into the scheduler loop.
            updated = await self._retry_store.mark_attempt_failed(
                retry_id=row.id, newly_failed_capability=newly_failed_capability, error=error,
            )
        except Exception as exc:  # never raise into the scheduler loop
            log.scheduler.error(
                "retry_actuator._handle_failure: mark_attempt_failed failed",
                exc_info=exc, extra={"_fields": {"retry_id": row.id}},
            )
            # Unknown terminal state (another caller may already own this row) —
            # "pending" is the conservative, non-data-losing report: worst case
            # is a harmless extra retry, never a silently dropped failure.
            return RetryOutcome(status="pending")
        if updated.status == "failed":
            await self._notify_gave_up(updated)
        return RetryOutcome(status=updated.status)

    async def _notify_gave_up(self, row: RetryQueueRow) -> None:
        if not row.channel_chat_id:
            return
        text = _STILL_FAILED_NOTICE.format(attempts=row.attempt_count, goal=row.goal)
        try:
            # channel_registry.get() raises ChannelNotFoundError for an
            # unregistered channel (channels/registry.py) — must stay inside
            # this try, notification is best-effort and must never raise into
            # attempt_retry's "never raise into the scheduler loop" contract.
            adapter = self._channel_registry.get(row.channel)
            await cast("_TargetedSender", adapter).send_text(
                text, chat_id=int(row.channel_chat_id)
            )
        except Exception as exc:  # notification best-effort
            log.telegram.error(
                "retry_actuator._notify_gave_up: notification send failed",
                exc_info=exc, extra={"_fields": {"retry_id": row.id}},
            )
