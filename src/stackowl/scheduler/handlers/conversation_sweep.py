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
Bakir's Q12 extends Hermes' rule from one condition to four because StackOwl has
autonomy machinery Hermes lacks; the store owns WHICH lanes expired, this handler
owns what "busy" MEANS, because that is the part that depends on the rest of the
platform.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from stackowl.infra.observability import log
from stackowl.scheduler.base import HandlerRegistry, JobHandler
from stackowl.scheduler.job import Job, JobResult

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.sessions.models import SessionEntry
    from stackowl.sessions.store import SessionStore


class ConversationSweepHandler(JobHandler):
    """Recurring sweep that finalises expired conversation lanes."""

    def __init__(
        self, store: SessionStore, *, process_registry: object | None = None,
        clarify_gateway: object | None = None,
    ) -> None:
        self._store = store
        self._process_registry = process_registry
        self._clarify_gateway = clarify_gateway

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

        TWO of Q12's four conditions are enforced here today: a running background
        process and a pending clarify. The other two — an in-flight DURABLE TASK
        and an ACTIVE OBJECTIVE — are NOT yet checked, and that is stated plainly
        rather than implied by an empty branch, because a busy-check that silently
        covers half its conditions is worse than one that admits the gap.
        """
        try:
            # Condition 1 — a background process still running on this lane.
            # ProcessRegistry.list() is session-scoped by default; a non-empty
            # result means this lane owns live process handles.
            registry = self._process_registry
            if registry is not None and registry.list(entry.session_key):  # type: ignore[attr-defined]
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

        duration_ms = (time.monotonic() - t0) * 1000
        # 4. EXIT
        log.scheduler.info(
            "[scheduler] conversation_sweep.execute: exit",
            extra={"_fields": {"job_id": job.job_id, "finalized": finalized,
                               "skipped_active": skipped, "duration_ms": duration_ms}},
        )
        return JobResult(
            job_id=job.job_id,
            effect_class="state_change",
            success=error is None,
            output=f"finalized={finalized} skipped_active={skipped}",
            error=error,
            duration_ms=duration_ms,
            metadata={"finalized": finalized, "skipped_active": skipped},
        )


def register_conversation_sweep_handler(
    store: SessionStore, *, process_registry: object | None = None,
    clarify_gateway: object | None = None,
) -> ConversationSweepHandler:
    """Construct and register the handler on the process HandlerRegistry."""
    handler = ConversationSweepHandler(
        store, process_registry=process_registry, clarify_gateway=clarify_gateway,
    )
    HandlerRegistry.instance().register(handler)
    log.scheduler.info(
        "[scheduler] conversation_sweep handler registered",
        extra={"_fields": {"handler": handler.handler_name,
                           "has_process_registry": process_registry is not None,
                           "has_clarify_gateway": clarify_gateway is not None}},
    )
    return handler
