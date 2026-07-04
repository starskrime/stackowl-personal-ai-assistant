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
from stackowl.interaction.reversibility_resolver import (
    Decision,
    Reversibility,
    ReversibilityResolver,
    reversibility_resolver_enabled,
)
from stackowl.objectives.decomposer import ObjectiveDecomposer
from stackowl.objectives.model import ExpectedOutcome, Objective, Subgoal, SubgoalSpec
from stackowl.objectives.store import ObjectiveStore
from stackowl.pipeline.acceptance import AcceptanceChecker
from stackowl.pipeline.recovery_actuator import Failure, RecoveryActuator
from stackowl.pipeline.state import PipelineState
from stackowl.providers.base import Message
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

# Task 3 (adaptive decomposition): a sub-goal the decomposer flagged as
# sufficiently complex is split one level deeper BEFORE it runs, instead of
# being forced into a single (likely too-coarse) turn. Threshold is on the
# decomposer's own 0.0-1.0 ``estimated_complexity`` scale (see
# ObjectiveDecomposer._build_prompt) — 0.7 picked so recursion fires only for a
# step that clearly bundles multiple actions, not every merely-nontrivial one.
_ADAPTIVE_DECOMPOSITION_THRESHOLD = 0.7

# Mirrors _MAX_SUBGOALS' cap-and-stop idiom, one level up: bounds how many
# times a sub-goal tree may be split so a persistently "complex" decomposer
# reply can never recurse without limit. A sub-goal's OWN
# ``decomposition_depth`` must be strictly less than this for it to be eligible
# for one more split (depth 0 = top-level, from the initial decomposition).
_MAX_DECOMPOSITION_DEPTH = 2

# Task 3 (recombination): borrows the SHAPE (system prompt + single user
# message → one LLM call → raw text), not the machinery, of
# ``parliament/synthesizer.py``'s synthesis prompt.
_RECOMBINATION_SYSTEM_PROMPT = (
    "You are a synthesis engine for a multi-step assistant objective that has "
    "just finished every one of its steps. Combine their individual results "
    "into ONE coherent, directly useful answer for the person who asked for the "
    "objective. Do not merely restate or concatenate the steps in order — "
    "actually synthesize them into a single combined answer. Reply in the same "
    "language the objective was stated in."
)
_RECOMBINATION_TIER = "powerful"
_RECOMBINATION_MAX_TOKENS = 512
_RECOMBINATION_TEMPERATURE = 0.2


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
        recovery: RecoveryActuator | None = None,
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
        # ADR-2 — the one recovery authority. The sub-goal retry-vs-escalate DECISION
        # delegates to its ``should_retry`` predicate (flag ``unify_objective_recovery``)
        # instead of an inline attempt-budget guard, so one policy governs every
        # subsystem's recovery. Stateless; injectable for tests.
        self._recovery = recovery or RecoveryActuator()

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
                job_id=job.job_id,
                effect_class="state_change", success=True,
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
            job_id=job.job_id,
            effect_class="state_change", success=True,
            output=f"advanced {advanced} objective(s)", error=None,
            duration_ms=duration_ms, metadata={"advanced": advanced},
        )

    # ------------------------------------------------------------- internals

    async def _advance(self, store: ObjectiveStore, objective: Objective) -> bool:
        """Advance one objective by its next pending sub-goal. Returns did-work."""
        nxt = await store.next_pending_subgoal(objective.objective_id)
        if nxt is None:
            # All sub-goals finished — the objective is complete. Recombine
            # (Task 3): synthesize a real combined answer from what the
            # sub-goals actually produced instead of echoing the original
            # intent text back verbatim.
            subgoals = await store.list_subgoals(objective.objective_id)
            summary = await self._synthesize_completion(objective, subgoals)
            await store.update_status(objective.objective_id, "done")
            await store.append_event(objective.objective_id, "completed", objective.intent)
            await self._notify(
                objective, f"✓ Objective complete: {objective.intent}\n\n{summary}"
            )
            log.scheduler.info(
                "[scheduler] objective_driver: objective complete",
                extra={"_fields": {"objective_id": objective.objective_id}},
            )
            return True

        # Task 3 (adaptive decomposition): a sufficiently complex sub-goal is
        # split one level deeper, in its own run-order slot, BEFORE it runs —
        # bounded by _MAX_DECOMPOSITION_DEPTH so this can never recurse without
        # limit. A successful split is a planning-only tick (did work, nothing
        # executed yet); the first child is picked up on the next tick.
        if (
            nxt.estimated_complexity >= _ADAPTIVE_DECOMPOSITION_THRESHOLD
            and nxt.decomposition_depth < _MAX_DECOMPOSITION_DEPTH
            and await self._maybe_decompose_further(store, objective, nxt)
        ):
            return True

        await store.update_subgoal(nxt.subgoal_id, "running")
        # F-43: don't run a retry COLD. When this sub-goal previously failed, the prior
        # attempt's failure reason is persisted in its ``result`` column; feed it back
        # into THIS run so the backend can pick a different approach instead of repeating
        # the failing one. A fresh sub-goal (no prior result) runs unchanged.
        run_description = self._with_retry_context(nxt)
        # Freshness clock for goal-level acceptance — captured BEFORE the run so a
        # stale pre-existing artifact cannot satisfy the declared outcome.
        started_at = time.time()
        final_state, task_id = await self._run_subgoal(
            objective, run_description, nxt.acceptance_criteria
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
        if used < _MAX_SUBGOAL_ATTEMPTS and self._may_retry(reason):
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

    def _may_retry(self, reason: str) -> bool:
        """Whether a failed sub-goal may be retried — the ONE recovery authority decides (ADR-2).

        When ``unify_objective_recovery`` is on (default) the retry-vs-escalate decision is
        delegated to :meth:`RecoveryActuator.should_retry` over a typed ``Failure`` instead of
        being re-decided inline. By the time a failure reaches the bounded-retry path it is
        non-consequential and transient-by-policy (an irreversible park or a verified-false
        step has already escalated to ``blocked`` upstream), so the authority returns True and
        the outcome is byte-identical to the inline budget gate — but the policy now lives in
        ONE place, and a consequential failure that ever reached here would be refused a retry
        by the same authority every other subsystem uses. Flag off ⇒ the inline gate decides
        alone (the actuator is not consulted), byte-identical to pre-ADR. A flag-read error
        fails safe to the unified path (the owner-approved default)."""
        if not self._unify_enabled():
            return True
        failure = Failure(
            name="objective_subgoal",
            kind="objective",
            transient=True,
            consequential=False,
            error=reason,
        )
        return self._recovery.should_retry(failure)

    def _unify_enabled(self) -> bool:
        """Read the ADR-2 ``unify_objective_recovery`` flag; default ON on any error.

        ``None`` settings (the unit surface) ⇒ the default (ON), so the authority governs
        the decision there too. A flag read must never break a driver tick."""
        if self._settings is None:
            return True
        try:
            return bool(self._settings.unify_objective_recovery)
        except Exception:  # noqa: BLE001 — a flag read must never sink the tick
            return True

    @staticmethod
    def _with_retry_context(subgoal: Subgoal) -> str:
        """Augment a previously-failed sub-goal's run with its prior failure (F-43).

        On a retry the sub-goal carries the prior attempt's failure reason in its
        ``result`` column (stamped when the bounded-retry path re-queued it to
        ``pending``, and preserved across an F-41 cooldown re-queue). Running the step
        COLD would simply repeat the failing approach; surfacing what already went wrong
        lets the backend choose a different one. This is OPERATIONAL within-turn context
        — reading a prior outcome to inform a retry — NOT persisted negative learning:
        nothing is written as a "doesn't work" lesson; the note exists only in this run's
        input_text. A first attempt (no prior result) returns the bare description, so the
        cold-start path is byte-identical. The ``result`` column is the subgoal-attributed
        source (objective ``events`` are not keyed to a specific sub-goal)."""
        prior = (subgoal.result or "").strip()
        if not prior:
            return subgoal.description
        log.scheduler.debug(
            "[scheduler] objective_driver._advance: feeding prior-failure context into retry",
            extra={"_fields": {
                "subgoal_id": subgoal.subgoal_id, "attempts": subgoal.attempts,
            }},
        )
        return (
            f"{subgoal.description}\n\n"
            "[Retry note] A previous attempt at this step did not succeed. "
            f"What went wrong last time: {prior}. "
            "Take a different approach; do not repeat what already failed."
        )

    @staticmethod
    def _park_is_irreversible(state: PipelineState) -> bool:
        """Classify a park as irreversible (needs a human) vs trivial/reversible (F-44).

        REUSES the consequential snapshot already threaded onto the turn rather than
        inventing a keyword list: a park that touched a consequential/irreversible tool
        (it appears in ``consequential_failures``) is a genuine ask-on-irreversible
        decision; a park with no consequential footprint is a trivial/reversible clarify
        the assistant may resolve itself with a best-effort default. Conservative on the
        boundary — when the snapshot is ambiguous we do NOT over-escalate, deferring to
        the consequential-failure signal that the execute step stamps explicitly.

        ADR-3: when ``settings.reversibility_resolver`` is ON this DELEGATES the
        escalate-or-not classification to the one :class:`ReversibilityResolver` — a
        consequential footprint maps to an ``irreversible`` signal, a clean park to
        ``reversible``, and ``must_reach_user`` reproduces ``bool(consequential_failures)``
        exactly (byte-identical). OFF ⇒ the inline check runs."""
        if reversibility_resolver_enabled():
            decision = Decision(
                reversibility=(
                    Reversibility.irreversible()
                    if state.consequential_failures
                    else Reversibility.reversible()
                )
            )
            return ReversibilityResolver.must_reach_user(decision)
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

    async def _maybe_decompose_further(
        self, store: ObjectiveStore, objective: Objective, subgoal: Subgoal
    ) -> bool:
        """Split ``subgoal`` one level deeper (Task 3 adaptive decomposition).

        Reuses the SAME :class:`ObjectiveDecomposer` used at objective-creation
        time on just this sub-goal's description — no bespoke recursive planner.
        The children are inserted at the sub-goal's own run-order slot (later
        sub-goals shift back) at ``decomposition_depth + 1``, and the now
        superseded parent row is deleted; ``add_subgoals``' cap-and-stop
        (``_MAX_SUBGOALS``) already bounds the child count, and the caller
        already checked the depth cap. Fail-safe: no provider registry wired, or
        a decomposition that resolves to a single child (nothing gained — the
        decomposer's own fail-safe fallback for an unparseable/failed reply),
        leaves the sub-goal untouched so it runs as-is THIS tick.
        """
        log.scheduler.debug(
            "[scheduler] objective_driver._maybe_decompose_further: entry",
            extra={"_fields": {
                "objective_id": objective.objective_id, "subgoal_id": subgoal.subgoal_id,
                "complexity": subgoal.estimated_complexity, "depth": subgoal.decomposition_depth,
            }},
        )
        if self._provider_registry is None:
            log.scheduler.debug(
                "[scheduler] objective_driver._maybe_decompose_further: no provider "
                "registry wired — running as-is",
                extra={"_fields": {"subgoal_id": subgoal.subgoal_id}},
            )
            return False
        decomposer = ObjectiveDecomposer(self._provider_registry)
        children: list[SubgoalSpec] = await decomposer.decompose_specs(subgoal.description)
        if len(children) < 2:
            log.scheduler.info(
                "[scheduler] objective_driver._maybe_decompose_further: no further "
                "split available — running as-is",
                extra={"_fields": {"subgoal_id": subgoal.subgoal_id}},
            )
            return False
        child_depth = subgoal.decomposition_depth + 1
        await store.insert_subgoals_at(
            objective.objective_id, subgoal.position, children, depth=child_depth,
        )
        await store.delete_subgoal(subgoal.subgoal_id)
        await store.append_event(
            objective.objective_id, "subgoal_decomposed",
            f"{subgoal.description[:80]} -> {len(children)} step(s) at depth {child_depth}",
        )
        log.scheduler.info(
            "[scheduler] objective_driver._maybe_decompose_further: exit",
            extra={"_fields": {
                "objective_id": objective.objective_id, "subgoal_id": subgoal.subgoal_id,
                "child_count": len(children), "depth": child_depth,
            }},
        )
        return True

    async def _synthesize_completion(
        self, objective: Objective, subgoals: list[Subgoal]
    ) -> str:
        """Combine every completed sub-goal's result into one coherent answer
        (Task 3 recombination), instead of echoing the original intent text back
        verbatim.

        Reuses the SAME single-call system-prompt/user-message SHAPE as
        :class:`stackowl.parliament.synthesizer.ParliamentSynthesizer` (not its
        multi-round/multi-owl machinery): one ``powerful``-tier call over the
        objective's intent plus each finished step's result. A trivial
        single-sub-goal objective skips the extra LLM round-trip entirely and
        surfaces that one result directly — a synthesis call would just restate
        it, at real latency/cost, for no benefit. Fail-safe throughout: no
        provider registry, a provider failure, or an empty reply all degrade to
        a legacy-shaped fallback so a synthesis miss can never swallow a
        completed objective's report.
        """
        fallback = f"Objective complete: {objective.intent}"
        done = [sg for sg in subgoals if sg.status == "done"]
        if not done:
            return fallback
        if len(done) == 1:
            return done[0].result or fallback
        if self._provider_registry is None:
            log.scheduler.debug(
                "[scheduler] objective_driver._synthesize_completion: no provider "
                "registry wired — concatenating step results",
                extra={"_fields": {"objective_id": objective.objective_id}},
            )
            return "\n".join(f"- {sg.description}: {sg.result}" for sg in done)

        log.scheduler.debug(
            "[scheduler] objective_driver._synthesize_completion: entry",
            extra={"_fields": {
                "objective_id": objective.objective_id, "subgoal_count": len(done),
            }},
        )
        lines = [f"Objective: {objective.intent}", ""]
        for sg in done:
            lines.append(f"Step: {sg.description}\nResult: {sg.result or '(no output)'}\n")
        messages = [
            Message(role="system", content=_RECOMBINATION_SYSTEM_PROMPT),
            Message(role="user", content="\n".join(lines)),
        ]
        try:
            provider, _degraded_from = self._provider_registry.resolve_capable_or_degrade(
                _RECOMBINATION_TIER
            )
            result = await provider.complete(
                messages,
                model="",
                max_tokens=_RECOMBINATION_MAX_TOKENS,
                temperature=_RECOMBINATION_TEMPERATURE,
            )
        except Exception as exc:  # noqa: BLE001 — a completed objective's report must still land
            log.scheduler.error(
                "[scheduler] objective_driver._synthesize_completion: provider call "
                "failed — falling back to the plain completion message",
                exc_info=exc,
                extra={"_fields": {"objective_id": objective.objective_id}},
            )
            return fallback
        text = (result.content or "").strip()
        log.scheduler.info(
            "[scheduler] objective_driver._synthesize_completion: exit",
            extra={"_fields": {
                "objective_id": objective.objective_id, "synthesized": bool(text),
            }},
        )
        return text or fallback

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
