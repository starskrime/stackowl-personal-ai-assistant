"""ConversationSweepHandler — makes the conversation boundary a CLOCK event (D01.7).

Without this, a lane only rolls over when the user next sends a message, so the
4 AM boundary really means "whenever you next say something" and Q17's overnight
summary never runs unattended — which is precisely when nobody is watching.

NAMING — read this before adding another "session" anything. There is already a
``session_sweep`` handler (``scheduler/handlers/session_sweep.py``) and it does
something entirely different: it reaps idle NAMED OWL SESSIONS from
``owls/session_registry.py``. This one sweeps CONVERSATION LANES. Registering
both under one ``handler_name`` would have silently lost one of them, so this is
``conversation_sweep``. That is the fourth distinct meaning of the word "session"
found in this tree during D01.7 — after parliament debates, browser pages, and
the conversation lane itself.

Mirrors ``clarify_sweep`` / ``session_sweep``: a :class:`JobHandler` subclass plus
a register factory. The recurring JOB row is seeded separately in
``scheduler/assembly.py``, alongside the other recurring handlers.

INVARIANT I4 LIVES HERE. A lane with work in flight is skipped, never forced.
Bakir's Q12 extends the reference platform' rule from one condition to four because StackOwl has
autonomy machinery the reference platform lacks; the store owns WHICH lanes expired, this handler
owns what "busy" MEANS, because that is the part that depends on the rest of the
platform.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from stackowl.infra.observability import log
from stackowl.scheduler.base import HandlerRegistry, JobHandler
from stackowl.scheduler.job import Job, JobResult

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.db.pool import DbPool
    from stackowl.sessions.models import SessionEntry
    from stackowl.sessions.store import SessionStore


class ConversationSweepHandler(JobHandler):
    """Recurring sweep that finalises expired conversation lanes."""

    def __init__(
        self, store: SessionStore, *, process_registry: object | None = None,
        clarify_gateway: object | None = None,
        enqueue_summary: Callable[..., Awaitable[bool]] | None = None,
        db: DbPool | None = None,
    ) -> None:
        self._store = store
        self._process_registry = process_registry
        self._clarify_gateway = clarify_gateway
        # Needed for Q12's task/objective conditions, which are owner-agnostic
        # lane queries rather than owner-scoped store reads. None → those two
        # conditions simply do not contribute, same degrade rule as the others.
        self._db = db
        # DEBT-11's backstop. Injected rather than imported so the scheduler does
        # not depend on memory/, and optional so an unwired backstop degrades to
        # exactly the previous behaviour instead of breaking expiry.
        self._enqueue_summary = enqueue_summary

    @property
    def handler_name(self) -> str:
        return "conversation_sweep"

    async def _is_busy(self, entry: SessionEntry) -> bool:
        """Invariant I4 — is this lane doing something that must not be cut?

        FAILS CLOSED. If we cannot tell whether a lane is busy we treat it as
        busy and let the next sweep decide: expiring a lane we could not assess
        risks severing work in flight, which is the harm this rule exists to
        prevent, whereas a delayed boundary costs a few minutes and nothing else.

        A component that is not wired contributes False rather than blocking every
        expiry forever — an absent subsystem is not evidence of activity.

        ALL FOUR of Q12's conditions are enforced here as of part 6. The last two
        needed migration 0099: neither `tasks` nor `objectives` recorded which
        conversation the work belonged to, so the question could not be asked at
        all.
        """
        try:
            # Condition 1 — a background process still running on this lane.
            # ProcessRegistry.list() is session-scoped by default; a non-empty
            # result means this lane owns live process handles.
            registry = self._process_registry
            if registry is not None and registry.list(entry.session_key):  # type: ignore[attr-defined]
                return True

            # Conditions 2 and 3 — a durable task in flight, or a live objective.
            # Both queries are deliberately OWNER-AGNOSTIC and filter on the lane;
            # see their docstrings. An owner-scoped read would match nothing here,
            # because objectives are created under DEFAULT_PRINCIPAL_ID while a
            # lane's identity is the person — which would make this rule a silent
            # no-op, the exact failure this item keeps turning up.
            if self._db is not None:
                from stackowl.objectives.store import any_active_objective_for_lane
                from stackowl.pipeline.durable.store import any_active_task_for_lane

                if await any_active_task_for_lane(self._db, entry.session_key):
                    return True
                if await any_active_objective_for_lane(self._db, entry.session_key):
                    return True

            # Condition 4 — a clarify question this lane is still waiting on. If
            # the lane rolled now, the parked turn would have nowhere to land;
            # that is a live bug class in clarify_pump, not a hypothetical (Q12).
            gateway = self._clarify_gateway
            if gateway is not None and gateway.peek_for_session(  # type: ignore[attr-defined]
                entry.session_key, entry.channel
            ):
                return True
        except Exception as exc:
            log.scheduler.error(
                "[scheduler] conversation_sweep: busy-check failed — treating the lane "
                "as BUSY so the boundary is delayed rather than severing live work",
                exc_info=exc,
                extra={"_fields": {"session_key": entry.session_key}},
            )
            return True
        return False

    async def execute(self, job: Job) -> JobResult:
        t0 = time.monotonic()
        # 1. ENTRY
        log.scheduler.debug(
            "[scheduler] conversation_sweep.execute: entry",
            extra={"_fields": {"job_id": job.job_id}},
        )
        finalized = skipped = 0
        error: str | None = None
        try:
            # 2. DECISION + 3. STEP — the store decides WHICH lanes expired.
            finalized, skipped = await self._store.sweep(is_busy=self._is_busy)
        except Exception as exc:  # self-healing — never raise into the scheduler loop
            error = str(exc)
            log.scheduler.error(
                "[scheduler] conversation_sweep.execute: sweep failed",
                exc_info=exc, extra={"_fields": {"job_id": job.job_id}},
            )

        recovered = await self._recover_lost_summaries(job)

        duration_ms = (time.monotonic() - t0) * 1000
        # 4. EXIT
        log.scheduler.info(
            "[scheduler] conversation_sweep.execute: exit",
            extra={"_fields": {"job_id": job.job_id, "finalized": finalized,
                               "skipped_active": skipped,
                               "summaries_recovered": recovered,
                               "duration_ms": duration_ms}},
        )
        return JobResult(
            job_id=job.job_id,
            effect_class="state_change",
            success=error is None,
            output=(f"finalized={finalized} skipped_active={skipped} "
                    f"summaries_recovered={recovered}"),
            error=error,
            duration_ms=duration_ms,
            metadata={"finalized": finalized, "skipped_active": skipped,
                      "summaries_recovered": recovered},
        )

    async def _recover_lost_summaries(self, job: Job) -> int:
        """Queue summaries for boundaries that were announced to nobody (DEBT-11).

        Publishing ``session.rollover`` is in-memory and fire-and-forget, so a
        boundary announced with no live consumer — or one whose consumer never got
        to enqueue because the core exec-replaced itself — is lost permanently:
        ``expiry_finalized`` then makes the double-announce guard suppress any
        second announcement. This is what makes Q15's durability real rather than
        conditional on a subscriber being alive at the right instant.

        Runs on EVERY sweep, unconditionally. It is safe to be dumb about it
        because ``jobs.idempotency_key`` is UNIQUE on
        ``rollover:{lane}:{incarnation}`` — a boundary already queued simply fails
        to insert again.

        The marker is written only when the enqueue SUCCEEDED, so a failure stays
        retryable on the next sweep rather than being silently dropped.
        """
        if self._enqueue_summary is None:
            return 0
        if self._db is None:
            # The backstop needs a db to enqueue against. Say so ONCE per sweep at
            # WARNING rather than calling with None and failing per lane: an
            # unwired backstop should look unwired, not broken.
            log.scheduler.warning(
                "[scheduler] conversation_sweep: summary backstop wired without a db "
                "— lost summaries cannot be recovered",
                extra={"_fields": {"job_id": job.job_id}},
            )
            return 0
        try:
            awaiting = await self._store.lanes_awaiting_summary()
        except Exception as exc:
            log.scheduler.error(
                "[scheduler] conversation_sweep: could not look for lost summaries",
                exc_info=exc, extra={"_fields": {"job_id": job.job_id}},
            )
            return 0

        recovered = 0
        for entry in awaiting:
            try:
                # `db` is a required POSITIONAL argument of
                # enqueue_rollover_summary(db, *, lane, ended, ...). Omitting it
                # raised TypeError on EVERY recovery attempt — the backstop
                # reported itself wired (has_summary_backstop: true at assembly)
                # and threw the moment it was actually used. It only surfaced on
                # 2026-08-16, because that is the first time a lane was found
                # awaiting a summary: a guard whose first execution is its first
                # test. Live evidence: conversation_sweep.py:199,
                # "missing 1 required positional argument: 'db'".
                queued = await self._enqueue_summary(
                    self._db,
                    lane=entry.session_key,
                    ended=entry.conversation_id,
                    identity_key=entry.identity_key,
                    owl_name=entry.owl_name,
                    channel=entry.channel,
                    reason=(entry.auto_reset_reason.value
                            if entry.auto_reset_reason else None),
                    message_count=entry.message_count,
                    completed_turns=entry.completed_turns,
                )
            except Exception as exc:
                # One bad lane must not stop the others being recovered.
                log.scheduler.error(
                    "[scheduler] conversation_sweep: summary recovery failed for a lane",
                    exc_info=exc,
                    extra={"_fields": {"session_key": entry.session_key,
                                       "conversation_id": entry.conversation_id}},
                )
                continue
            if not queued:
                continue
            await self._store.mark_summary_enqueued(entry.session_key,
                                                    entry.conversation_id)
            recovered += 1
            log.scheduler.info(
                "[scheduler] conversation_sweep: recovered a lost summary",
                extra={"_fields": {"session_key": entry.session_key,
                                   "conversation_id": entry.conversation_id}},
            )
        return recovered


def register_conversation_sweep_handler(
    store: SessionStore, *, process_registry: object | None = None,
    clarify_gateway: object | None = None,
    enqueue_summary: Callable[..., Awaitable[bool]] | None = None,
    db: DbPool | None = None,
) -> ConversationSweepHandler:
    """Construct and register the handler on the process HandlerRegistry."""
    handler = ConversationSweepHandler(
        store, process_registry=process_registry, clarify_gateway=clarify_gateway,
        enqueue_summary=enqueue_summary, db=db,
    )
    HandlerRegistry.instance().register(handler)
    log.scheduler.info(
        "[scheduler] conversation_sweep handler registered",
        extra={"_fields": {"handler": handler.handler_name,
                           "has_process_registry": process_registry is not None,
                           "has_clarify_gateway": clarify_gateway is not None,
                           "has_summary_backstop": enqueue_summary is not None,
                           "enforces_all_four_i4_conditions": db is not None}},
    )
    return handler
