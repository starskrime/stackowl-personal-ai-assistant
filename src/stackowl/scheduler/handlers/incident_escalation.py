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
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from stackowl.infra.clock import Clock, WallClock
from stackowl.infra.observability import log
from stackowl.learning.failure_outcome_miner import (
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
    from stackowl.learning.failure_outcome_miner import CapabilityTagLookup
    from stackowl.memory.outcome_store import TaskOutcomeStore
    from stackowl.scheduler.handlers.health_sweep import HealthSweepHandler

_LOOKBACK_DAYS_DEFAULT = 7
_SECONDS_PER_DAY = 86_400
_MIN_RECURRENCE = 3  # mirrors FailureOutcomeMiner._MIN_EVIDENCE

Retryability = Literal["non_retryable", "analyze"]


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
    ) -> None:
        self._health = health_sweep
        self._outcomes = outcome_store
        self._rca = rca_session
        self._capability_tag_lookup = capability_tag_lookup
        self._clock = clock or WallClock()
        self._recurrence_threshold = recurrence_threshold
        self._lookback_days = lookback_days
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
            self._open_incidents[inc.signature] = incident_id  # dedupe BEFORE running
            verdict, ran_rca = await self._resolve_incident(inc, incident_id)
            if verdict is not None:
                self.verdicts[inc.key] = verdict
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
            samples = tuple(
                f"- trace={o.trace_id} tools={list(o.tool_sequence)} "
                f"failure_class={o.failure_class} input={(o.input_text or '')[:120]!r}"
                for o in cluster.outcomes[:5]
            )
            incidents[sig] = _Incident(
                signature=sig,
                capability_class=cluster.capability_class,
                failure_class=cluster.failure_class,
                kind="outcome",
                parent_trace_ids=tuple(o.trace_id for o in cluster.outcomes[:10]),
                brief=(
                    f"{cluster.size} failed task outcomes for capability "
                    f"'{cluster.capability_class}' all with failure_class "
                    f"'{cluster.failure_class}' within the last "
                    f"{self._lookback_days}d — recurring past the in-turn "
                    f"self-heal (retry/substitution/floor) that already ran.\n"
                    + "\n".join(samples)
                ),
            )
        return incidents

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
        except Exception:  # noqa: BLE001
            return time.time()
