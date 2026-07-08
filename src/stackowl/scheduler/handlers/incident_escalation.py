"""IncidentEscalationHandler (ADR-6 self-heal, Task 6) — detect a recurring,
self-heal-DIDN'T-fix-it incident and drive a staged root-cause analysis.

Sibling to :class:`~stackowl.scheduler.handlers.health_sweep.HealthSweepHandler`.
Where the sweep is the DETECT+RECYCLE half of the loop (recycle a down subsystem,
re-verify, alert if still unhealthy), this handler is the ESCALATE half: when
the ordinary recycle/retry/substitution machinery has ALREADY run and FAILED, a
subsystem or capability is still broken on a later tick — that is no longer a
transient blip, it's an incident worth a real diagnosis.

Three trigger sources — all read from DURABLE, inspectable state
----------------------------------------------------------------
1. **Subsystem still unhealthy after recycle.** Reuses the alert-state map the
   ``HealthSweepHandler`` already maintains (``_alert_state``: subsystem name ->
   (status, monotonic)). A name present there is a subsystem that survived the
   sweep's heal→re-verify and is STILL down/degraded — i.e. ``ensure_available()``
   already ran and the failure persisted. We read that map rather than building a
   second health tracker (the sweep is the single source of health truth).
2. **Bridging-substitution recurrence** (``delivery_gate._BRIDGING_RECOVERY_KINDS
   = {"substitution"}``) and **3. structural-veto / never-empty-floor recurrence**
   (``pipeline/supervisor.py``). Both of those recovery paths are TURN-SCOPED
   (``recovery_context`` ContextVars, the supervisor tally) — they cannot be read
   from a scheduler tick, which runs outside any turn. Their DURABLE footprint is
   what we key on instead: when substitution/veto keep firing for the same
   capability yet the turn STILL fails, it lands as a failed ``TaskOutcome`` row
   (``failure_class`` set, ``tool_sequence`` naming the capability). A cluster of
   such rows for one ``(capability_class, failure_class)`` crossing the recurrence
   threshold IS the observable "self-heal recurred and still failed" signal. We
   reuse Task 5's :func:`cluster_failures_by_capability_and_signature` verbatim so
   the incident grain matches the miner that consumes the verdict.

Dedupe — ONE incident, ONE RCA session
--------------------------------------
``_open_incidents`` maps a stable signature -> minted incident_id. A signature
already open is SKIPPED on later ticks (so a subsystem that stays degraded for an
hour produces ONE RCA session, not one per 5-minute tick); a signature that
clears is dropped so it can re-open later. This is the same identity the sweep's
alert-state map dedupes on, extended to the outcome-cluster signatures.

Transient-vs-structural gate (first thing, before spending an RCA cycle)
------------------------------------------------------------------------
:func:`classify_incident_retryability` grounds the decision in the REAL exception
hierarchy (``stackowl.exceptions``), not guessed keywords: an
``InfrastructureError``/timeout-shaped failure that RECURS past self-heal is worth
a diagnosis (run the 3 stages); a deterministic ``DomainError`` (the capability
fundamentally can't do this — unsupported action, missing provider, validation)
is non-retryable, so we short-circuit to a substitution/"alternative-needed"
verdict WITHOUT burning an RCA cycle (the AWS-Bedrock-retry-guidance shape).

Scope: this handler STOPS at "here is a (verified or fallback) RcaVerdict",
stored on ``self.verdicts`` keyed by ``(capability_class, failure_class)``.
Consuming those verdicts (into tool_build / capability_substitution /
delegate_task, and feeding the ``FailureOutcomeMiner``) is Task 7, not here.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from stackowl.infra.clock import Clock, WallClock
from stackowl.infra.observability import log
from stackowl.learning.failure_outcome_miner import (
    FailureCluster,
    RcaVerdict,
    cluster_failures_by_capability_and_signature,
)
from stackowl.parliament.staged_rca import (
    RcaEvidence,
    StagedRcaSession,
    fallback_verdict,
)
from stackowl.scheduler.base import JobHandler
from stackowl.scheduler.job import Job, JobResult

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.learning.failure_outcome_miner import (
        CapabilityTagLookup,
        FailureOutcomeMiner,
    )
    from stackowl.memory.outcome_store import TaskOutcome, TaskOutcomeStore
    from stackowl.scheduler.handlers.health_sweep import HealthSweepHandler

# Task 7 consumption hooks — optional, None-default (byte-identical no-op when
# unwired, matching every other optional-service field in this codebase).
# ``VerdictRouter`` dispatches a NEW verdict to the real tool_build /
# capability_substitution consumers (see rca_verdict_router.py); ``AlertSink``
# mirrors HealthSweepHandler's own alert-sink type so an incident verdict rides
# the SAME operator-alert channel, not a new one.
VerdictRouter = Callable[[RcaVerdict, Literal["fix", "alternative"]], Awaitable[None]]
AlertSink = Callable[[str], Awaitable[None]]

_LOOKBACK_DAYS_DEFAULT = 7
_SECONDS_PER_DAY = 86_400
_MIN_RECURRENCE = 3  # mirrors FailureOutcomeMiner._MIN_EVIDENCE

# Synthetic failure_class for a SOURCE-3 (masked-recurring-substitution) incident:
# there is no real exception (the turn succeeded), so this is not a
# stackowl.exceptions name. classify_incident_retryability resolves it to
# "analyze" via its unknown-name fallback (never skip a diagnosis on
# uncertainty) — exactly right: WHY the underlying capability keeps failing is
# precisely what is unknown here.
_MASKED_SUBSTITUTION_FAILURE_CLASS = "RecurringSubstitutionMask"

Retryability = Literal["non_retryable", "analyze"]


def _capability_class_for(tool: str, tag_lookup: CapabilityTagLookup | None) -> str:
    """Resolve *tool*'s capability grain: its registered ``capability_tag``, or
    the raw tool name when none is registered.

    ponytail: duplicates ``failure_outcome_miner._capability_class_for`` (a
    private name) rather than importing it across the module boundary — Task 5's
    module is read-only for this task. Same one-line body, same fallback.
    """
    if tag_lookup is None:
        return tool
    tag = tag_lookup(tool)
    return tag if tag else tool


def _incident_escalation_enabled() -> bool:
    """ADR-6 flag read — shares the ``health_loop`` master switch so the escalate
    half only runs when the self-heal loop is ON. Module-level so tests can
    monkeypatch it. Never raises."""
    try:
        from stackowl.config.settings import Settings

        return bool(Settings().health_loop)
    except Exception:  # noqa: BLE001 — a flag read must never wedge the sweep
        return False


def classify_incident_retryability(failure_class: str) -> Retryability:
    """Ground the transient-vs-structural decision in the REAL exception hierarchy.

    ``failure_class`` is an exception CLASS NAME (from ``classify_failure`` in
    ``outcome_store.py``) — a stable code identifier, not natural-language text,
    so resolving it against :mod:`stackowl.exceptions` is legitimate (not the
    hardcoded-keyword antipattern). The split:

    * ``InfrastructureError`` subtree (or any ``*Timeout*`` name) — a transient/
      infra failure that RECURRED past the recycle/retry loop. Retry alone is not
      fixing it, so a real root-cause diagnosis is warranted → ``"analyze"``.
    * ``DomainError`` subtree — a deterministic domain/config failure (unsupported
      action, missing provider/owl/channel, validation, parse). Retrying or
      recycling the SAME capability is doomed; the fix is always an alternative →
      ``"non_retryable"`` (short-circuit to a substitution verdict, no RCA cycle).
    * Anything that does not resolve to a known exception class (e.g. a health
      status like ``"down"``, or a truncated fallback string) → ``"analyze"``:
      never SKIP a diagnosis on uncertainty.
    """
    from stackowl import exceptions as exc_mod

    name = (failure_class or "").split(".")[-1].strip()
    cls = getattr(exc_mod, name, None)
    if not isinstance(cls, type) or not issubclass(cls, BaseException):
        return "analyze"
    if issubclass(cls, exc_mod.InfrastructureError) or "Timeout" in cls.__name__:
        return "analyze"
    if issubclass(cls, exc_mod.DomainError):
        return "non_retryable"
    return "analyze"


@dataclass(frozen=True)
class _Incident:
    """One detected incident awaiting (or short-circuiting) an RCA."""

    signature: str
    capability_class: str
    failure_class: str
    brief: str
    kind: Literal["health", "outcome"]
    parent_trace_ids: tuple[str, ...] = ()

    @property
    def key(self) -> tuple[str, str]:
        return (self.capability_class, self.failure_class)


class IncidentEscalationHandler(JobHandler):
    """Detect recurring self-heal-didn't-fix incidents → staged RCA verdict.

    Reuses the sweep's alert-state map (health incidents) + Task 5's failure
    clustering (substitution/veto recurrence footprint), dedupes to one RCA per
    incident, and drives :class:`StagedRcaSession` (fixed stages, not debate).
    """

    def __init__(
        self,
        *,
        health_sweep: HealthSweepHandler,
        outcome_store: TaskOutcomeStore,
        rca_session: StagedRcaSession,
        capability_tag_lookup: CapabilityTagLookup | None = None,
        clock: Clock | None = None,
        recurrence_threshold: int = _MIN_RECURRENCE,
        lookback_days: int = _LOOKBACK_DAYS_DEFAULT,
        # Task 7 — thin consumption hooks. All None-default: an unwired
        # handler behaves byte-identically to Task 6 (stops at self.verdicts).
        verdict_router: VerdictRouter | None = None,
        miner: FailureOutcomeMiner | None = None,
        alert: AlertSink | None = None,
    ) -> None:
        self._health = health_sweep
        self._outcomes = outcome_store
        self._rca = rca_session
        self._capability_tag_lookup = capability_tag_lookup
        self._clock = clock or WallClock()
        self._recurrence_threshold = recurrence_threshold
        self._lookback_days = lookback_days
        self._verdict_router = verdict_router
        self._miner = miner
        self._alert = alert
        # Dedupe: signature -> minted incident_id. A signature already here is an
        # OPEN incident (its RCA already ran); later ticks skip it. Cleared when
        # the signature is no longer active so it can re-open later.
        self._open_incidents: dict[str, str] = {}
        # Verdicts produced this process, keyed by (capability_class,
        # failure_class) — the exact map Task 7 / FailureOutcomeMiner.mine consume.
        self.verdicts: dict[tuple[str, str], RcaVerdict] = {}

    @property
    def handler_name(self) -> str:
        return "incident_escalation"

    async def execute(self, job: Job) -> JobResult:
        # 1. ENTRY
        log.scheduler.debug(
            "[scheduler] incident_escalation.execute: entry",
            extra={"_fields": {"job_id": job.job_id}},
        )
        t0 = time.monotonic()
        if not _incident_escalation_enabled():
            log.scheduler.debug(
                "[scheduler] incident_escalation.execute: flag off — noop",
                extra={"_fields": {"job_id": job.job_id}},
            )
            return JobResult(
                job_id=job.job_id, effect_class="read_only", success=True,
                output="disabled", error=None,
                duration_ms=(time.monotonic() - t0) * 1000.0,
            )

        try:
            active = await self._detect_incidents()
        except Exception as exc:  # detection must never wedge the scheduler
            duration_ms = (time.monotonic() - t0) * 1000.0
            log.scheduler.error(
                "[scheduler] incident_escalation.execute: detection raised",
                exc_info=exc,
                extra={"_fields": {"job_id": job.job_id, "duration_ms": duration_ms}},
            )
            return JobResult(
                job_id=job.job_id, effect_class="read_only", success=False,
                output=None, error=str(exc), duration_ms=duration_ms,
            )

        # 2. DECISION — drop cleared incidents, then act ONLY on NEW signatures
        # (dedupe: one incident → one RCA session, never one per tick).
        for sig in list(self._open_incidents):
            if sig not in active:
                del self._open_incidents[sig]
        new_incidents = [inc for sig, inc in active.items() if sig not in self._open_incidents]

        analyzed = 0
        short_circuited = 0
        for inc in new_incidents:
            incident_id = f"incident-{uuid.uuid4().hex[:12]}"
            verdict, ran_rca = await self._resolve_incident(inc, incident_id)
            # Only mark the signature "handled" (dedupe closed) when a verdict was
            # ACTUALLY produced (verified OR explicitly rejected — both are a real
            # RcaVerdict object; see staged_rca._build_verdict). A hard RCA failure
            # (a stage backend error/timeout, an empty stage, an unparseable
            # response) returns verdict=None — do NOT register the signature then,
            # so the NEXT tick retries the RCA for this same persistent incident
            # instead of silently giving up on it forever after one failed attempt
            # (the exact "silent fail, no retry" antipattern this arc exists to
            # kill — a provider outage during the incident is precisely when the
            # RCA call itself is most likely to also fail).
            if verdict is not None:
                self._open_incidents[inc.signature] = incident_id
                self.verdicts[inc.key] = verdict
                # Task 7 hook — a short-circuited fallback_verdict (ran_rca=False,
                # the non-retryable/deterministic-domain-failure path) is always an
                # "alternative-needed" verdict; a verdict that came out of the full
                # 3-stage RCA (ran_rca=True) is a proposed "fix". This is the exact
                # signal _resolve_incident already computes — no new classification.
                kind: Literal["fix", "alternative"] = "fix" if ran_rca else "alternative"
                await self._consume_verdict(inc, verdict, kind)
            else:
                log.scheduler.warning(
                    "[scheduler] incident_escalation: RCA produced no verdict — "
                    "NOT marking handled, will retry next tick",
                    extra={"_fields": {"incident_id": incident_id, "signature": inc.signature}},
                )
            if ran_rca:
                analyzed += 1
            else:
                short_circuited += 1

        # 4. EXIT
        duration_ms = (time.monotonic() - t0) * 1000.0
        log.scheduler.info(
            "[scheduler] incident_escalation.execute: exit",
            extra={"_fields": {
                "job_id": job.job_id,
                "active": len(active),
                "new": len(new_incidents),
                "analyzed": analyzed,
                "short_circuited": short_circuited,
                "open": len(self._open_incidents),
                "duration_ms": duration_ms,
            }},
        )
        return JobResult(
            job_id=job.job_id, effect_class="read_only", success=True,
            output=f"active={len(active)} new={len(new_incidents)} "
            f"analyzed={analyzed} short_circuited={short_circuited}",
            error=None, duration_ms=duration_ms,
            metadata={
                "active": len(active), "new": len(new_incidents),
                "analyzed": analyzed, "short_circuited": short_circuited,
            },
        )

    async def _detect_incidents(self) -> dict[str, _Incident]:
        """Gather active incident signatures from BOTH durable sources."""
        incidents: dict[str, _Incident] = {}

        # SOURCE 1 — subsystems the sweep already recycled + re-verified STILL
        # unhealthy (its alert-state map is the single health-truth store).
        # Minor known gap: a subsystem with NO registered HealableResource never
        # gets a recycle attempt at all (health_sweep._heal_and_verify no-ops for
        # it), so this can fire on its FIRST unhealthy tick rather than strictly
        # "after a recycle already failed". Low impact — dedupe still holds (one
        # incident, not one per tick) and an un-healered subsystem stuck unhealthy
        # is arguably a legitimate incident regardless.
        alert_state: dict[str, tuple[str, float]] = getattr(
            self._health, "_alert_state", {},
        )
        for name, (status, _ts) in alert_state.items():
            sig = f"health:{name}:{status}"
            incidents[sig] = _Incident(
                signature=sig,
                capability_class=name,
                failure_class=status,
                kind="health",
                brief=(
                    f"Subsystem '{name}' is still {status} after an automated "
                    f"recycle (ensure_available) already ran and the failure "
                    f"persisted across sweep ticks. This is not a transient blip."
                ),
            )

        # SOURCE 2 — recurring failed outcomes: the durable footprint of a
        # substitution/veto that kept firing yet the turn still failed.
        since = self._clock_time() - self._lookback_days * _SECONDS_PER_DAY
        try:
            outcomes = await self._outcomes.list_failed_global(since_epoch=since)
        except AttributeError:
            log.scheduler.warning(
                "[scheduler] incident_escalation: outcome_store has no "
                "list_failed_global — skipping outcome incidents",
            )
            outcomes = []
        clusters = cluster_failures_by_capability_and_signature(
            list(outcomes), min_size=self._recurrence_threshold,
            capability_tag_lookup=self._capability_tag_lookup,
        )
        for cluster in clusters:
            sig = f"outcome:{cluster.capability_class}:{cluster.failure_class}"
            if sig in incidents:  # a health incident already owns this signature
                continue
            # Fake-incident guard: a cluster with ZERO precisely-attributed rows
            # (every member has failed_capability=None, i.e. the turn's failure
            # was never pinned on a specific tool) exists only because the
            # clustering fallback credits EVERY tool named in a long, sprawling
            # turn's tool_sequence. A frequently-called innocent tool (skill_view,
            # memory, tool_search...) then "recurs" across many unrelated failed
            # turns by pure co-occurrence, not because it's actually broken.
            # Escalating that to a full RCA produces a confidently-worded but
            # WRONG "structurally broken" verdict (2026-07-08 incident: skill_view
            # was blamed this way — see project_skill_view_false_incident_rejected
            # memory).
            #
            # 2026-07-08 follow-up (shell misattribution): requiring only ONE
            # precisely-attributed row let a cluster with 1 real row + many
            # noise rows still escalate — and ALL of cluster.outcomes (including
            # the noise) was fed to the RCA as "evidence", so the analyzer
            # concluded shell was a common thread across turns where a
            # DIFFERENT tool (owl_build, skill_manage) was the actual, self-
            # reported failure. Fix: gate AND build evidence from the
            # precisely-attributed subset only — one real occurrence is not
            # "recurring", and noise rows must never dilute the narrative.
            precise_outcomes = self._precisely_attributed_outcomes(cluster)
            if len(precise_outcomes) < self._recurrence_threshold:
                log.scheduler.info(
                    "[scheduler] incident_escalation: too few precisely-attributed "
                    "rows to recur on — skipping (co-occurrence noise diluted the "
                    "raw cluster)",
                    extra={"_fields": {
                        "capability": cluster.capability_class,
                        "failure_class": cluster.failure_class,
                        "cluster_size": cluster.size,
                        "precise_count": len(precise_outcomes),
                        "threshold": self._recurrence_threshold,
                    }},
                )
                continue
            samples = tuple(
                f"- trace={o.trace_id} tools={list(o.tool_sequence)} "
                f"failure_class={o.failure_class} input={(o.input_text or '')[:120]!r}"
                for o in precise_outcomes[:5]
            )
            incidents[sig] = _Incident(
                signature=sig,
                capability_class=cluster.capability_class,
                failure_class=cluster.failure_class,
                kind="outcome",
                parent_trace_ids=tuple(o.trace_id for o in precise_outcomes[:10]),
                brief=(
                    f"{len(precise_outcomes)} failed task outcomes for capability "
                    f"'{cluster.capability_class}' all with failure_class "
                    f"'{cluster.failure_class}' within the last "
                    f"{self._lookback_days}d — recurring past the in-turn "
                    f"self-heal (retry/substitution/floor) that already ran.\n"
                    + "\n".join(samples)
                ),
            )

        # SOURCE 3 — recurring BRIDGED substitution (migration 0077,
        # ``recovered_via_tool``). A bridged turn is a trustworthy SUCCESS
        # (failure_class=NULL) and is INVISIBLE to SOURCE 2/list_failed_global —
        # this is the masked-chronic-outage shape: the same capability recovering
        # via substitution turn after turn, with zero real fix ever attempted
        # ("permanent fallback with zero retry"). Clustered separately since these
        # rows carry no failure_class of their own.
        try:
            recovered = await self._outcomes.list_recovered_global(since_epoch=since)
        except AttributeError:
            log.scheduler.warning(
                "[scheduler] incident_escalation: outcome_store has no "
                "list_recovered_global — skipping masked-substitution incidents",
            )
            recovered = []
        by_capability: dict[str, list[TaskOutcome]] = {}
        for o in recovered:
            if not o.recovered_via_tool:
                continue
            capability = _capability_class_for(o.recovered_via_tool, self._capability_tag_lookup)
            by_capability.setdefault(capability, []).append(o)
        for capability, members in by_capability.items():
            if len(members) < self._recurrence_threshold:
                continue
            sig = f"substitution:{capability}"
            if sig in incidents:
                continue
            samples = tuple(
                f"- trace={o.trace_id} recovered_via_tool={o.recovered_via_tool} "
                f"input={(o.input_text or '')[:120]!r}"
                for o in members[:5]
            )
            incidents[sig] = _Incident(
                signature=sig,
                capability_class=capability,
                failure_class=_MASKED_SUBSTITUTION_FAILURE_CLASS,
                kind="outcome",
                parent_trace_ids=tuple(o.trace_id for o in members[:10]),
                brief=(
                    f"{len(members)} turns in the last {self._lookback_days}d had "
                    f"'{capability}' fail and get silently BRIDGED by a capability "
                    f"substitution — every turn 'worked' (no failed outcome row "
                    f"exists for any of these), but the underlying capability is "
                    f"chronically broken and has never actually been fixed or "
                    f"retried. This is a permanent fallback masking an outage.\n"
                    + "\n".join(samples)
                ),
            )
        return incidents

    def _precisely_attributed_outcomes(self, cluster: FailureCluster) -> list[TaskOutcome]:
        """The SUBSET of *cluster*'s outcomes that are real evidence for
        ``cluster.capability_class`` — filtering out ambiguous co-occurrence
        credits before they ever reach an incident brief or the RCA analyzer.

        A row counts as real evidence when either: (a) ``failed_capability``
        itself names this capability (an actual unrecovered/raised failure was
        pinned on it), or (b) the row is UNAMBIGUOUS — its ``tool_sequence``
        maps to exactly one capability class, so "blamed by co-occurrence"
        and "blamed because it's the only suspect" are the same thing (a
        single-tool turn has no fan-out to be wrong about).

        What this excludes: a row from a long, multi-capability turn where
        ``failed_capability`` is ``None`` (the failure was never pinned on a
        specific tool — e.g. a goal-level acceptance refutation) AND several
        DIFFERENT capabilities appear in ``tool_sequence``. Crediting every one
        of those as "the recurring offender" is how a frequently-called,
        perfectly healthy tool (skill_view, memory, tool_search...) gets framed
        for an incident it had nothing to do with — see
        project_skill_view_false_incident_rejected memory (2026-07-08).
        """
        precise: list[TaskOutcome] = []
        for o in cluster.outcomes:
            if o.failed_capability is not None:
                if (
                    _capability_class_for(o.failed_capability, self._capability_tag_lookup)
                    == cluster.capability_class
                ):
                    precise.append(o)
                continue
            row_capabilities = {
                _capability_class_for(tool, self._capability_tag_lookup)
                for tool in o.tool_sequence
            }
            if row_capabilities == {cluster.capability_class}:
                precise.append(o)
        return precise

    async def _consume_verdict(
        self, inc: _Incident, verdict: RcaVerdict, kind: Literal["fix", "alternative"],
    ) -> None:
        """Task 7 hook — route a NEW verdict to the real fix/alternative
        consumer, alert the operator WITH the verdict (not a bare status
        flap), and let Task 5's miner consider authoring a learned skill.

        Every step is independently best-effort (B5): a consumer failure
        never blocks dedup or the next tick's detection — this handler still
        STOPS at "here is a verdict"; consumption failures are logged, not
        propagated.
        """
        log.scheduler.debug(
            "[scheduler] incident_escalation._consume_verdict: entry",
            extra={"_fields": {
                "signature": inc.signature, "kind": kind, "verified": verdict.verified,
            }},
        )
        if self._verdict_router is not None:
            try:
                await self._verdict_router(verdict, kind)
            except Exception as exc:  # B5 — a router failure must not wedge the tick
                log.scheduler.error(
                    "[scheduler] incident_escalation: verdict router failed",
                    exc_info=exc, extra={"_fields": {"signature": inc.signature}},
                )
        if self._alert is not None and verdict.verified:
            try:
                await self._alert(_compose_verdict_alert(inc, verdict, kind))
            except Exception as exc:  # alert failure must not fail the sweep itself
                log.scheduler.error(
                    "[scheduler] incident_escalation: alert sink raised",
                    exc_info=exc, extra={"_fields": {"signature": inc.signature}},
                )
        elif self._alert is not None:
            # An unverified verdict (the verifier stage rejected or couldn't
            # confirm the hypothesis) is exactly the kind of noise operators
            # asked to stop seeing — log it for anyone reading the JSONL trace,
            # but do not push it to the operator chat as if it were confirmed.
            log.scheduler.info(
                "[scheduler] incident_escalation: verdict UNVERIFIED — suppressing "
                "chat alert (logged only)",
                extra={"_fields": {"signature": inc.signature}},
            )
        if self._miner is not None:
            try:
                # Mine only THIS verdict, not the full accumulated self.verdicts
                # history — every prior verdict's cluster was already mined (and
                # is idempotently skipped if re-mined) in the tick it was first
                # added, so re-passing the whole map on every new incident just
                # re-scans/re-checks every OLD signature again for no benefit
                # (visible as a "skill already exists — skip" line per old
                # signature, every single tick, forever).
                report = await self._miner.mine({inc.key: verdict})
                log.scheduler.info(
                    "[scheduler] incident_escalation: miner pass",
                    extra={"_fields": {
                        "n_clusters": report.n_clusters_found,
                        "n_written": report.n_skills_written,
                    }},
                )
            except Exception as exc:  # B5 — a mining failure must not wedge the tick
                log.scheduler.error(
                    "[scheduler] incident_escalation: miner.mine failed",
                    exc_info=exc, extra={"_fields": {"signature": inc.signature}},
                )

    async def _resolve_incident(
        self, inc: _Incident, incident_id: str,
    ) -> tuple[RcaVerdict | None, bool]:
        """Classify, then either short-circuit to a fallback verdict (non-retryable)
        or run the 3-stage RCA. Returns ``(verdict, ran_rca)``."""
        evidence = RcaEvidence(
            incident_id=incident_id,
            capability_class=inc.capability_class,
            failure_class=inc.failure_class,
            brief=inc.brief,
            parent_trace_ids=inc.parent_trace_ids,
        )
        retryability = classify_incident_retryability(inc.failure_class)
        log.scheduler.info(
            "[scheduler] incident_escalation: incident opened",
            extra={"_fields": {
                "incident_id": incident_id, "signature": inc.signature,
                "kind": inc.kind, "retryability": retryability,
            }},
        )
        if retryability == "non_retryable":
            # 2. DECISION — known non-retryable: skip the RCA cycle, go straight to
            # the substitution/alternative-needed verdict.
            verdict = fallback_verdict(
                evidence,
                reason=f"{inc.failure_class} is a deterministic domain failure",
            )
            log.scheduler.info(
                "[scheduler] incident_escalation: non-retryable — fallback verdict "
                "(no RCA cycle)",
                extra={"_fields": {
                    "incident_id": incident_id, "failure_class": inc.failure_class,
                }},
            )
            return verdict, False

        # 3. STEP — worth analyzing: run the fixed 3-stage RCA.
        rca_verdict = await self._rca.analyze(evidence)
        log.scheduler.info(
            "[scheduler] incident_escalation: RCA complete",
            extra={"_fields": {
                "incident_id": incident_id,
                "verified": rca_verdict.verified if rca_verdict else None,
            }},
        )
        return rca_verdict, True

    def _clock_time(self) -> float:
        """Wall-clock epoch seconds for the lookback window. ``Clock.now()`` is a
        tz-aware datetime; fall back to ``time.time()`` if unavailable."""
        try:
            return self._clock.now().timestamp()
        except Exception as exc:  # never a silent except
            log.scheduler.debug(
                "[scheduler] incident_escalation: clock.now() failed — using time.time()",
                exc_info=exc,
            )
            return time.time()


def _compose_verdict_alert(
    inc: _Incident, verdict: RcaVerdict, kind: Literal["fix", "alternative"],
) -> str:
    """Human-readable operator alert carrying the RCA verdict — Task 7's
    guaranteed-delivery requirement for the background/async incident path
    (the common case: incidents are detected from a scheduler tick, no live
    turn). Mirrors ``health_sweep._compose_alert``'s plain-text shape but
    names the ROOT CAUSE + FIX instead of a bare 'down'/'degraded' flap."""
    header = "🔎 Incident RCA verdict" + (" (verified)" if verdict.verified else " (unverified)")
    lines = [
        header,
        f"  capability: {inc.capability_class}  failure: {inc.failure_class}",
        f"  kind: {kind}",
        f"  root cause: {verdict.root_cause.strip()}",
        f"  fix/alternative: {verdict.fix_pattern.strip()}",
    ]
    return "\n".join(lines)
