"""ObjectiveDriverHandler — advance standing objectives autonomously (1C).

The functional heart of the Objective Manager. A seeded scheduler job fires this
handler on a short cadence; each tick it advances every ACTIVE objective by its
next pending sub-goal, runs that sub-goal through the pipeline backend (durably
when ``durable.goals`` is on, else ephemerally — mirroring goal_execution),
records progress + an activity event, and decides:

* more pending sub-goals → keep going next tick (no notification — avoid spam);
* all sub-goals done → mark the objective ``done`` and notify the owner once;
* a sub-goal PARKS (a consequential/irreversible action it cannot get consent
  for in a non-interactive context) or FAILS → mark the objective ``blocked``
  and notify the owner. This is the act-on-reversible / ask-on-irreversible
  posture realized autonomously: the assistant works the reversible steps on its
  own and surfaces only the irreversible decision.

Delivery reuses the durable exactly-once seam (:class:`ProactiveJobDeliverer`)
by adapting the objective's own recipient columns into a synthetic delivery
``Job`` — the driver's seeded job has no per-objective recipient.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from stackowl.infra.observability import log
from stackowl.objectives.model import ExpectedOutcome, Objective, Subgoal
from stackowl.objectives.store import ObjectiveStore
from stackowl.pipeline.acceptance import AcceptanceChecker
from stackowl.pipeline.state import PipelineState
from stackowl.scheduler.base import JobHandler, TriggerKind
from stackowl.scheduler.job import Job, JobResult
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID

if TYPE_CHECKING:  # pragma: no cover — typing only
    from stackowl.config.settings import Settings
    from stackowl.db.pool import DbPool
    from stackowl.notifications.proactive_job import ProactiveJobDeliverer
    from stackowl.pipeline.backends.base import OrchestratorBackend
    from stackowl.providers.registry import ProviderRegistry

_HANDLER = "objective_driver"
_CATEGORY = "objective"

# Bounded retry budget per sub-goal before the objective escalates to ``blocked``
# (F-40). A single transient stumble must not permanently strand the goal: while a
# sub-goal stays under this ceiling, a failure leaves it ``pending`` so the next
# driver tick retries it. Small by design — this is operational resilience against
# the transient, not an open-ended loop on a genuinely impossible step.
_MAX_SUBGOAL_ATTEMPTS = 3

# F-41: how long a TRANSIENT-blocked objective must sit before the driver re-queues
# it. A blocked objective used to be abandoned forever (the loop scans active-only);
# now a transient-class block (the retry budget was spent on a flaky step) is given
# a cooldown backoff, after which the stuck sub-goal's attempt budget is reset and
# the objective returns to ``active`` for a fresh try. A ``decision``-class block
# (genuinely irreversible / verified-false) is NEVER auto-requeued — it waits for a
# human. The clock is the objective's ``updated_at`` (stamped when it blocked).
_BLOCKED_RETRY_COOLDOWN_S = 600.0


class ObjectiveDriverHandler(JobHandler):
    """Advance every active objective by one sub-goal per scheduler tick."""

    def __init__(
        self,
        db: DbPool | None,
        backend: OrchestratorBackend | None,
        *,
        settings: Settings | None = None,
        job_deliverer: ProactiveJobDeliverer | None = None,
        provider_registry: ProviderRegistry | None = None,
        owner_id: str = DEFAULT_PRINCIPAL_ID,
        blocked_retry_cooldown_s: float = _BLOCKED_RETRY_COOLDOWN_S,
    ) -> None:
        self._db = db
        self._backend = backend
        # F-41 cooldown before a TRANSIENT-blocked objective is re-queued. Injectable
        # so tests can drive the recovery without wall-clock waits.
        self._blocked_retry_cooldown_s = blocked_retry_cooldown_s
        # Gates durable routing per sub-goal (read live for hot-reload), mirroring
        # GoalExecutionHandler — flag off (default) ⇒ legacy ephemeral path.
        self._settings = settings
        # The durable exactly-once delivery seam. None ⇒ no notification (back-
        # compat / unit surface); never a fake "delivered".
        self._job_deliverer = job_deliverer
        # Provider access for the OPTIONAL post-hoc LLM acceptance layer. None (or
        # an empty acceptance_tier) ⇒ that layer is never reached (byte-identical).
        self._provider_registry = provider_registry
        self._owner_id = owner_id
        # Goal-level acceptance authority (verification B3). Stateless; deterministic
        # filesystem observation of a sub-goal's declared ExpectedOutcome.
        self._acceptance = AcceptanceChecker()

    @property
    def handler_name(self) -> str:
        return _HANDLER

    @property
    def trigger_kind(self) -> TriggerKind:
        # Seeded with a standing every-1m row in SchedulerAssembly, so the boot
        # wiring audit does not flag it as dangling.
        return "seeded"

    async def execute(self, job: Job) -> JobResult:
        # 1. ENTRY
        t0 = time.monotonic()
        log.scheduler.debug(
            "[scheduler] objective_driver.execute: entry",
            extra={"_fields": {"job_id": job.job_id, "has_db": self._db is not None}},
        )
        if self._db is None or self._backend is None:
            return JobResult(
                job_id=job.job_id, success=True,
                output="objective_driver: noop (no db/backend)", error=None,
                duration_ms=(time.monotonic() - t0) * 1000,
            )

        store = ObjectiveStore(self._db, self._owner_id)
        # F-41: first rescue any TRANSIENT-blocked objective whose cooldown has elapsed
        # — return it to ``active`` so the very same tick can advance it (no abandonment).
        requeued = await self._requeue_recoverable(store)
        active = await store.list_objectives(status="active")
        log.scheduler.debug(
            "[scheduler] objective_driver.execute: active objectives",
            extra={"_fields": {"count": len(active), "requeued": requeued}},
        )

        advanced = 0
        for objective in active:
            try:
                if await self._advance(store, objective):
                    advanced += 1
            except Exception as exc:  # noqa: BLE001 — one objective must not sink the tick
                log.scheduler.error(
                    "[scheduler] objective_driver.execute: objective advance failed",
                    exc_info=exc,
                    extra={"_fields": {"objective_id": objective.objective_id}},
                )

        duration_ms = (time.monotonic() - t0) * 1000
        # 4. EXIT
        log.scheduler.info(
            "[scheduler] objective_driver.execute: exit",
            extra={"_fields": {"advanced": advanced, "duration_ms": duration_ms}},
        )
        return JobResult(
            job_id=job.job_id, success=True,
            output=f"advanced {advanced} objective(s)", error=None,
            duration_ms=duration_ms, metadata={"advanced": advanced},
        )

    # ------------------------------------------------------------- internals

    async def _advance(self, store: ObjectiveStore, objective: Objective) -> bool:
        """Advance one objective by its next pending sub-goal. Returns did-work."""
        nxt = await store.next_pending_subgoal(objective.objective_id)
        if nxt is None:
            # All sub-goals finished — the objective is complete.
            await store.update_status(objective.objective_id, "done")
            await store.append_event(objective.objective_id, "completed", objective.intent)
            await self._notify(objective, f"✓ Objective complete: {objective.intent}")
            log.scheduler.info(
                "[scheduler] objective_driver: objective complete",
                extra={"_fields": {"objective_id": objective.objective_id}},
            )
            return True

        await store.update_subgoal(nxt.subgoal_id, "running")
        # Freshness clock for goal-level acceptance — captured BEFORE the run so a
        # stale pre-existing artifact cannot satisfy the declared outcome.
        started_at = time.time()
        final_state, task_id = await self._run_subgoal(
            objective, nxt.description, nxt.acceptance_criteria
        )
        response_text = "".join(c.content for c in final_state.responses)

        if final_state.durable_parked:
            blocker = "; ".join(final_state.errors) or "awaiting a decision"
            if self._park_is_irreversible(final_state):
                # ASK-ON-IRREVERSIBLE: a genuinely consequential/irreversible decision
                # the assistant must not make unilaterally — block + ping the owner.
                await store.update_subgoal(
                    nxt.subgoal_id, "blocked", result=blocker, task_id=task_id
                )
                await store.update_status(
                    objective.objective_id, "blocked",
                    blocker=blocker, blocker_kind="decision",
                )
                await store.append_event(objective.objective_id, "blocked", blocker)
                await self._notify(
                    objective,
                    f"⏸ Objective needs your decision: {objective.intent}\n{blocker}",
                )
                return True
            # ACT-ON-REVERSIBLE (F-44): a trivial/reversible clarify that parked only
            # because there is no human in this non-interactive context. Stranding the
            # whole objective on it is over-escalation. Auto-resolve with the sensible
            # default — defer to the bounded-retry path (act-first next tick), logged —
            # so only genuinely irreversible choices ever reach the owner.
            log.scheduler.info(
                "[scheduler] objective_driver: reversible park — auto-resolving with "
                "default (deferring to retry), not escalating to blocked",
                extra={"_fields": {
                    "objective_id": objective.objective_id,
                    "subgoal_id": nxt.subgoal_id, "blocker": blocker,
                }},
            )
            await self._on_subgoal_failure(store, objective, nxt, blocker, task_id)
            return True

        if final_state.errors:
            err = "; ".join(final_state.errors)
            await self._on_subgoal_failure(store, objective, nxt, err, task_id)
            return True

        # Goal-level acceptance (verification B3). When the sub-goal DECLARED an
        # expected outcome, a clean run is not enough — the declared post-condition
        # must be observed against reality. This catches the class the per-tool
        # `verified` net cannot (a tool that exits 0 producing nothing, e.g. a shell
        # no-op). No declaration ⇒ the checker no-ops ⇒ the legacy no-error path
        # (byte-identical). When NO criterion was declared, the OPTIONAL post-hoc
        # LLM layer (flag-gated, fail-closed) may derive one from the draft.
        criteria = nxt.acceptance_criteria or await self._derive_acceptance(
            objective.intent, nxt.description, response_text
        )
        verdict = self._acceptance.check(
            criteria,
            turn_started_at=started_at,
            # The turn acted if it produced a response or dispatched a tool — a
            # pure no-op turn is never penalized for an outcome it had no chance to
            # produce. A confident "done!" text IS an action, so a claim-without-
            # artifact is still caught.
            acted=bool(final_state.responses or final_state.tool_calls),
        )
        if verdict.accepted is False:
            # A DECLARED post-condition was refuted by reality — this is a VERIFIED
            # failure (the turn claimed an outcome it did not produce), not a
            # transient execution stumble. It escalates to ``blocked`` immediately
            # (it is not subject to the F-40 transient-error retry budget): a clean
            # retry of a step whose effect was measured-absent would just re-assert
            # the same false claim. The owner is notified.
            reason = f"step did not achieve its goal: {verdict.reason}"
            await store.update_subgoal(
                nxt.subgoal_id, "failed", result=reason, task_id=task_id, verified=False,
            )
            # A clean retry would only re-assert the same measured-absent claim, so this
            # is NOT transient-recoverable — it waits for a human (blocker_kind=decision).
            await store.update_status(
                objective.objective_id, "blocked", blocker=reason, blocker_kind="decision",
            )
            await store.append_event(objective.objective_id, "subgoal_failed", reason)
            await self._notify(objective, f"⚠ Objective stalled: {objective.intent}\n{reason}")
            log.scheduler.info(
                "[scheduler] objective_driver: sub-goal failed acceptance",
                extra={"_fields": {
                    "objective_id": objective.objective_id,
                    "subgoal_id": nxt.subgoal_id, "reason": verdict.reason,
                }},
            )
            return True

        # Done. Stamp the HONEST verification disposition (F-42): when a criterion
        # was declared/derived and observed, verified=True; when NONE was available
        # (the default — no declared criterion AND the LLM deriver off), the clean
        # run is NOT proof of effect, so the sub-goal completes UNVERIFIED
        # (verified=False) rather than over-claiming a verified success.
        verified = verdict.accepted is True
        await store.update_subgoal(
            nxt.subgoal_id, "done", result=response_text, task_id=task_id,
            verified=verified,
        )
        await store.append_event(objective.objective_id, "subgoal_done", nxt.description)
        return True

    async def _on_subgoal_failure(
        self,
        store: ObjectiveStore,
        objective: Objective,
        subgoal: Subgoal,
        reason: str,
        task_id: str | None,
    ) -> None:
        """Handle a sub-goal failure with a bounded retry budget (F-40).

        ``subgoal.attempts`` is the count BEFORE this run; this run is one more, so
        the new total is ``attempts + 1``. While that stays UNDER the ceiling the
        sub-goal is returned to ``pending`` (objective stays ``active``, no owner
        ping — the next tick simply retries). Only once the budget is exhausted does
        the objective escalate to ``blocked`` and the owner get notified, exactly as
        before. The attempt count is operational retry state, never a learned lesson."""
        used = subgoal.attempts + 1
        if used < _MAX_SUBGOAL_ATTEMPTS:
            # Transient stumble: leave it pending so the next tick retries it. The
            # whole objective stays active — a single failure no longer strands it.
            await store.update_subgoal(
                subgoal.subgoal_id, "pending", result=reason,
                task_id=task_id, attempts=used,
            )
            await store.append_event(
                objective.objective_id, "subgoal_retry",
                f"attempt {used}/{_MAX_SUBGOAL_ATTEMPTS}: {reason}",
            )
            log.scheduler.info(
                "[scheduler] objective_driver: sub-goal failed — retrying",
                extra={"_fields": {
                    "objective_id": objective.objective_id,
                    "subgoal_id": subgoal.subgoal_id,
                    "attempt": used, "max": _MAX_SUBGOAL_ATTEMPTS,
                }},
            )
            return
        # Budget exhausted — escalate to blocked and notify the owner. This is a
        # TRANSIENT-class block (F-41): the step stalled on execution errors, so after a
        # cooldown the driver will re-queue the objective for a fresh attempt budget
        # rather than abandoning it. Nothing here is mined as a learned lesson.
        await store.update_subgoal(
            subgoal.subgoal_id, "failed", result=reason,
            task_id=task_id, attempts=used,
        )
        await store.update_status(
            objective.objective_id, "blocked", blocker=reason, blocker_kind="transient",
        )
        await store.append_event(objective.objective_id, "subgoal_failed", reason)
        await self._notify(objective, f"⚠ Objective stalled: {objective.intent}\n{reason}")

    @staticmethod
    def _park_is_irreversible(state: PipelineState) -> bool:
        """Classify a park as irreversible (needs a human) vs trivial/reversible (F-44).

        REUSES the consequential snapshot already threaded onto the turn rather than
        inventing a keyword list: a park that touched a consequential/irreversible tool
        (it appears in ``consequential_failures``) is a genuine ask-on-irreversible
        decision; a park with no consequential footprint is a trivial/reversible clarify
        the assistant may resolve itself with a best-effort default. Conservative on the
        boundary — when the snapshot is ambiguous we do NOT over-escalate, deferring to
        the consequential-failure signal that the execute step stamps explicitly."""
        return bool(state.consequential_failures)

    async def _requeue_recoverable(self, store: ObjectiveStore) -> int:
        """Return TRANSIENT-blocked objectives to ``active`` after their cooldown (F-41).

        A ``decision``-class block (or an unclassified legacy block, treated as
        ``decision``) is left untouched — it genuinely needs a human. A ``transient``
        block is re-queued once ``updated_at`` is older than the cooldown: the stuck
        sub-goal is reset to ``pending`` with a fresh attempt budget so the next advance
        retries it. Returns how many objectives were recovered."""
        blocked = await store.list_objectives(status="blocked")
        now = datetime.now(tz=UTC)
        recovered = 0
        for objective in blocked:
            if objective.blocker_kind != "transient":
                continue  # decision / legacy → stays blocked until a human steps in
            age_s = (now - objective.updated_at).total_seconds()
            if age_s < self._blocked_retry_cooldown_s:
                continue  # still cooling down
            # Reset the stalled sub-goal (failed/blocked) to pending with a fresh budget.
            for subgoal in await store.list_subgoals(objective.objective_id):
                if subgoal.status in ("failed", "blocked"):
                    await store.update_subgoal(
                        subgoal.subgoal_id, "pending", attempts=0,
                    )
                    break
            await store.update_status(objective.objective_id, "active")
            await store.append_event(
                objective.objective_id, "requeued",
                f"transient block cooldown elapsed ({age_s:.0f}s) — retrying",
            )
            log.scheduler.info(
                "[scheduler] objective_driver: re-queued transient-blocked objective",
                extra={"_fields": {
                    "objective_id": objective.objective_id, "age_s": age_s,
                }},
            )
            recovered += 1
        return recovered

    async def _run_subgoal(
        self,
        objective: Objective,
        description: str,
        acceptance_criteria: ExpectedOutcome | None = None,
    ) -> tuple[PipelineState, str | None]:
        """Run one sub-goal through the pipeline; returns (final_state, task_id)."""
        assert self._backend is not None  # narrowed by execute()
        trace_id = f"objgoal-{uuid.uuid4().hex[:8]}"
        state = PipelineState(
            trace_id=trace_id,
            session_id=f"objective-{objective.objective_id}",
            input_text=description,
            channel=objective.channel or "cli",
            owl_name="secretary",
            pipeline_step="",
            # No human present to answer a clarify; the handler owns delivery.
            interactive=False,
            defer_delivery=True,
            # Carry the declared post-condition onto the turn so downstream layers
            # (and the future LLM-derived acceptance) can see it. The driver itself
            # performs the authoritative deterministic check after the run.
            expected_outcome=acceptance_criteria,
        )
        if self._durable_enabled():
            from stackowl.pipeline.durable.store import DurableTaskStore
            from stackowl.pipeline.durable.task_runner import DurableTaskRunner

            assert self._db is not None  # narrowed by _durable_enabled
            store = DurableTaskStore(self._db, self._owner_id)
            runner = DurableTaskRunner(store, self._backend)
            final_state, task_id = await runner.run(goal=description, state=state)
            return final_state, task_id

        final_state = await self._backend.run(state)
        return final_state, None

    async def _derive_acceptance(
        self, intent: str, description: str, draft: str
    ) -> ExpectedOutcome | None:
        """OPTIONAL post-hoc LLM-derived acceptance (verification B3, flag-OFF default).

        Returns a derived ExpectedOutcome ONLY when ``settings.acceptance_tier`` is
        set AND a provider registry is wired. FAIL-CLOSED by construction (the
        deriver returns None on any model error/garbage) and never raises — an
        unreachable model yields no expectation, so the sub-goal falls back to its
        prior (deterministic / no-error) signal. None on every default path."""
        tier = self._settings.acceptance_tier if self._settings is not None else ""
        if not tier or self._provider_registry is None:
            return None
        from stackowl.pipeline.acceptance_llm import LlmAcceptanceDeriver

        deriver = LlmAcceptanceDeriver(self._provider_registry, tier)
        intent_for_draft = description or intent
        return await deriver.derive(intent=intent_for_draft, draft=draft)

    def _durable_enabled(self) -> bool:
        """True iff durable sub-goal routing is on AND a DbPool is wired."""
        if self._settings is None or self._db is None:
            return False
        return bool(self._settings.durable.goals)

    async def _notify(self, objective: Objective, message: str) -> None:
        """Deliver a progress/blocked message to the objective's owner, honestly."""
        if self._job_deliverer is None:
            log.scheduler.debug(
                "[scheduler] objective_driver._notify: no deliverer wired — skipping",
                extra={"_fields": {"objective_id": objective.objective_id}},
            )
            return
        synthetic = self._delivery_job(objective)
        try:
            outcome = await self._job_deliverer.deliver_for_job(
                synthetic, message=message, category=_CATEGORY, urgency="normal",
            )
            log.scheduler.info(
                "[scheduler] objective_driver._notify: delivered",
                extra={"_fields": {
                    "objective_id": objective.objective_id, "rollup": outcome.rollup,
                }},
            )
        except Exception as exc:  # noqa: BLE001 — a notify failure must not sink the tick
            log.scheduler.error(
                "[scheduler] objective_driver._notify: delivery raised",
                exc_info=exc,
                extra={"_fields": {"objective_id": objective.objective_id}},
            )

    @staticmethod
    def _delivery_job(objective: Objective) -> Job:
        """Adapt an objective's durable recipient into a synthetic delivery Job.

        The driver's seeded job has no per-objective recipient; DeliverySpec reads
        ``target_channels`` / ``target_addresses`` off a Job, so we carry the
        objective's own columns through a throwaway Job. A unique idempotency key
        makes each notification a distinct delivery-ledger occurrence.
        """
        now = datetime.now(tz=UTC).isoformat()
        return Job(
            job_id=f"objective-{objective.objective_id}",
            handler_name=_HANDLER,
            schedule="every 1m",
            idempotency_key=f"objective-{objective.objective_id}-{uuid.uuid4().hex[:8]}",
            last_run_at=None,
            next_run_at=now,
            status="running",
            primary_channel=objective.channel,
            target_channels=list(objective.target_channels),
            target_addresses=dict(objective.target_addresses),
        )
