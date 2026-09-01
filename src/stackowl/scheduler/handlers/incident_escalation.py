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

import json
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
    from stackowl.memory.bridge import MemoryBridge
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

#: How far above the platform's OWN failure rate a capability must sit before its
#: failures are an incident rather than the cost of doing business.
#:
#: WHY A COUNT WAS NEVER ENOUGH. `_MIN_RECURRENCE` is an absolute count with no
#: denominator under it, so any capability the platform uses regularly crosses it
#: permanently. MEASURED 2026-09-01 over seven days and 2,395 turns:
#:
#:     capability          failed  ran in   rate     z
#:     browser_navigate        66     163   40.5%  14.8
#:     web_fetch               60     145   41.4%  14.4
#:     shell                   36     158   22.8%   6.6
#:     read_file               14     148    9.5%   0.5   <- pooled was 8.35%
#:     todo                     3      25   12.0%   0.7
#:
#: `read_file` at 9.5% against a platform-wide 8.35% is indistinguishable from
#: normal, and `todo` was three failures total — yet each opened an incident and
#: each bought a ~140,000-token staged RCA that concluded nothing. That is why 10
#: signatures produced 145 incidents and 19,167,115 tokens in 26 hours, ~64% of
#: ALL platform spend, with 86 of 100 verdicts coming back unverified. There was
#: no defect to find.
#:
#: THREE SIGMA, AND IT IS NOT A TUNING KNOB. The bar is one-sided ~0.1%: the
#: chance this capability's failures came from the same process as everything
#: else's. It is scale-aware where a bare rate is not — 3-in-25 and 60-in-145 are
#: both "above average" and only the second is distinguishable from chance. And
#: the baseline is the PLATFORM'S OWN rate, so a fleet-wide bad day raises the bar
#: rather than opening an incident against every tool at once.
_ANOMALY_Z = 3.0

# Synthetic failure_class for a SOURCE-3 (masked-recurring-substitution) incident:
# there is no real exception (the turn succeeded), so this is not a
# stackowl.exceptions name. classify_incident_retryability resolves it to
# "analyze" via its unknown-name fallback (never skip a diagnosis on
# uncertainty) — exactly right: WHY the underlying capability keeps failing is
# precisely what is unknown here.
_MASKED_SUBSTITUTION_FAILURE_CLASS = "RecurringSubstitutionMask"

Retryability = Literal["non_retryable", "analyze", "defer"]

#: Failures meaning "there is no model available to think with". Held as
#: exception CLASSES, not names — same structural discipline as D02.6.
#:
#: MEASURED 2026-08-05. On 2026-07-29 the LLM backend was down and the platform
#: attempted 1,294 turns against a normal day's 60-150. 871 of them were the RCA
#: channel (hypothesis 292, rca_gatherer 291, verifier 288) — invisible on a
#: healthy day. The loop was: provider dies -> turns fail -> failures open
#: incidents -> RCA runs to diagnose them -> RCA's own three stages call the same
#: dead provider -> more failures. The self-healing machinery consumed the outage
#: as input and multiplied it ~10x, producing 258 barren verdicts that day.
#:
#: A failure whose cause IS "the diagnostic engine is unreachable" cannot be
#: diagnosed BY the diagnostic engine. The correct action is to wait.
_SUBSTRATE_UNAVAILABLE: tuple[str, ...] = (
    "AllProvidersUnavailableError",  # cascade found every provider OPEN
    "CircuitOpenError",              # this provider's breaker is open
)
# DELIBERATELY NOT HERE: RateLimitError. It was in the first version of this
# tuple and an existing test caught it — correctly. A rate limit is OUR limiter
# fail-closed (F124), not the substrate vanishing: a zero refill_rate is a
# genuine misconfiguration worth diagnosing, and the RCA may route to a
# different tier anyway. It also appeared ZERO times in the measured failure
# distribution, so deferring it was scope I had no evidence for.


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
    * The LLM substrate being unavailable (:data:`_SUBSTRATE_UNAVAILABLE`) →
      ``"defer"``. Diagnosing "the model is unreachable" REQUIRES the model, so
      an RCA here cannot succeed and its three stages become three more failures.
      This is not uncertainty — it is the one case where we know analysis is
      impossible, so it is exempt from the never-skip-on-uncertainty rule below.
      The condition is self-resolving; the incident re-detects when it isn't.

    * Anything that does not resolve to a known exception class (e.g. a health
      status like ``"down"``, or a truncated fallback string) → ``"analyze"``:
      never SKIP a diagnosis on uncertainty.
    """
    from stackowl import exceptions as exc_mod

    name = (failure_class or "").split(".")[-1].strip()
    # Checked BEFORE the class resolution below: these are InfrastructureError
    # subclasses, so the "analyze" branch would otherwise claim them — which is
    # exactly what it did, 290 times in one day.
    if name in _SUBSTRATE_UNAVAILABLE:
        return "defer"
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


#: How many consecutive verdict-less RCA attempts one signature may make before the
#: handler stops retrying it and registers it as handled.
#:
#: MUST BE > 1. The retry itself is deliberate and its reasoning is preserved: "a
#: provider outage during the incident is precisely when the RCA call itself is most
#: likely to also fail", so giving up after a single failure would bury a signature
#: for a transient reason. What was missing is the OTHER half — a ceiling. A retry
#: without one is not persistence, it is a loop, and this one costs ~70,000 input
#: tokens per attempt.
#:
#: 3 is the smallest number that survives a transient outage (which rarely spans
#: three scheduler ticks) while bounding the loop. This is deliberately NOT the
#: broader suppression policy question — see ESC-66 — because bounding a retry that
#: never succeeds cannot suppress a diagnosis that would have worked: a signature
#: whose RCA produces a verdict registers on its FIRST attempt and never reaches
#: this path.
#: The audit event that records "this signature has had an RCA".
#:
#: THE SUPPRESSION ALREADY EXISTED AND LIVED IN RAM. `_open_incidents` and
#: `_verdict_failures` are instance dicts — perfect within one process, EMPTY the
#: moment it is replaced, and this platform exec-replaces its core on every commit.
#: MEASURED 2026-08-31: incident lanes spent 15,724,829 input tokens, 72.3% of ALL
#: spend, over 126 RCA sessions averaging 125,181 tokens; 86 of 100 completions
#: concluded verified=False; and the detector reported the SAME seven or eight
#: incidents as "new" on every tick, all day. With one RCA per tick the in-memory
#: dedup could not even converge before the next restart wiped it.
#:
#: THE DURABLE STORE IS THE ONE ALREADY USED FOR THIS EXACT PURPOSE. `audit_log`
#: carries capability.denied / capability.escalated, and `find_recurring_gaps`
#: skips a pair that already has the escalated marker "so a gap that fires every
#: run alerts once rather than every sweep". Same pattern, no new table.
DIAGNOSED_EVENT = "incident.diagnosed"

#: How long a diagnosis suppresses its signature.
#:
#: ARITHMETIC, NOT TASTE: at 24 hours and roughly eight live signatures this is at
#: most eight RCAs a day — about 1M tokens against today's 15.7M. And it EXPIRES,
#: because a problem still present tomorrow deserves a fresh look: a stale
#: diagnosis of a changed system is worth less than a new one.
_DIAGNOSIS_GOOD_FOR_HOURS = 24.0


#: An analysis that STARTED. Written before the RCA runs, so an attempt that never
#: returns still leaves a trace.
#:
#: MEASURED 2026-09-01 over the retained logs: `[rca] staged.analyze: entry` 462
#: times against `exit` 355 — **107 analyses (23%) never finished** — and 85 of
#: those 107 had a RESTART as their next event. There were **232 boots in four
#: days**, one every ~25 minutes, because CodeWatcher exec-replaces the core on
#: every commit. A staged RCA budgets up to 960s. Work that takes sixteen minutes
#: cannot survive a process that is replaced every twenty-five, and roughly one in
#: five never did.
#:
#: NOTHING RECORDED IT. `record_diagnosis` is written on COMPLETION only, so an
#: interrupted analysis left the signature looking "never diagnosed" — the next
#: tick started the identical analysis from zero, spent another ~140,000 tokens,
#: and was interrupted again. 85 x 140k is roughly 12M tokens that produced no
#: verdict, no record, and no way to notice.
#:
#: A START MARKER SUPPRESSES TOO, and that is the point rather than a side effect.
#: Four days of evidence say an analysis interrupted once will be interrupted
#: again; re-entering it every ten minutes is the furnace. The 24h expiry still
#: applies, so a genuinely-needed analysis is retried tomorrow, and the incident
#: itself is still DETECTED every tick — only the expensive re-analysis is held.
STARTED_EVENT = "incident.diagnosis_started"


async def record_diagnosis_started(
    db: object, *, signature: str, incident_id: str, now: float | None = None,
) -> None:
    """Record that an RCA for *signature* has BEGUN. Never raises.

    Written before the analysis so a process replacement mid-flight still leaves
    the signature accounted for. Same ledger, same key (``actor`` = signature) as
    :func:`record_diagnosis`, so :func:`recently_diagnosed` reads both with one
    query and there is no second store to drift.

    Args:
        db: The pool. A ledger write may never fail a sweep.
        signature: The incident signature this analysis is for.
        incident_id: The minted id, for correlation with the log.
        now: Injectable clock for tests.
    """
    stamp = time.time() if now is None else now
    try:
        await db.execute(  # type: ignore[attr-defined]
            "INSERT INTO audit_log (event_type, actor, target, timestamp, details, "
            "integrity_hash, chain_version) VALUES (?,?,?,?,?,?,?)",
            (STARTED_EVENT, signature, incident_id, stamp,
             json.dumps({"completed": False}), "", "v1"),
        )
    except Exception as exc:  # noqa: BLE001 — a ledger write may not cost a tick
        log.scheduler.warning(
            "[scheduler] incident_escalation: could not record the analysis start "
            "— an interruption here will look like it never ran",
            exc_info=exc, extra={"_fields": {"signature": signature}},
        )


async def record_diagnosis(
    db: object, *, signature: str, incident_id: str,
    verified: bool | None = None, now: float | None = None,
) -> None:
    """Record that *signature* has had an RCA. Never raises.

    VERIFIED OR NOT. 86 of today's 100 RCAs concluded verified=False; if only a
    verified verdict suppressed, those 86 would keep re-running for ever — which is
    precisely what happened. "We looked and could not explain it" is still an
    answer, and re-deriving it every ten minutes costs 125,000 tokens a time.

    Bookkeeping must never fail a sweep, so a write error is logged and swallowed.
    """
    stamp = time.time() if now is None else now
    try:
        await db.execute(  # type: ignore[attr-defined]
            "INSERT INTO audit_log (event_type, actor, target, timestamp, details, "
            "integrity_hash, chain_version) VALUES (?,?,?,?,?,?,?)",
            (DIAGNOSED_EVENT, signature, incident_id, stamp,
             json.dumps({"verified": verified}), "", "v1"),
        )
    except Exception as exc:  # noqa: BLE001 — a ledger write may not cost a tick
        log.scheduler.warning(
            "[scheduler] incident_escalation: could not record the diagnosis — "
            "this signature may be re-diagnosed after a restart",
            exc_info=exc, extra={"_fields": {"signature": signature}},
        )


async def interrupted_diagnoses(db: object, *, now: float | None = None) -> list[str]:
    """Signatures whose analysis STARTED in the window and never completed.

    The measurement that was impossible before :data:`STARTED_EVENT` existed: an
    RCA killed by a process replacement left no trace at all, so 85 of 462
    analyses over four days were invisible — the tokens were spent, no verdict
    was produced, and the next tick began the same work.

    Args:
        db: The pool.
        now: Injectable clock for tests.

    Returns:
        Sorted signatures started-but-not-finished; empty on any read failure,
        which the caller must treat as "cannot tell", never as "none".
    """
    stamp = time.time() if now is None else now
    since = stamp - _DIAGNOSIS_GOOD_FOR_HOURS * 3600.0
    try:
        rows = await db.fetch_all(  # type: ignore[attr-defined]
            "SELECT DISTINCT s.actor AS actor FROM audit_log s "
            "WHERE s.event_type = ? AND s.timestamp >= ? AND s.actor IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM audit_log d WHERE d.event_type = ? "
            "AND d.actor = s.actor AND d.timestamp >= s.timestamp)",
            (STARTED_EVENT, since, DIAGNOSED_EVENT),
        )
    except Exception as exc:  # noqa: BLE001 — a metric may never cost a tick
        log.scheduler.warning(
            "[scheduler] incident_escalation: could not read interrupted analyses",
            exc_info=exc,
        )
        return []
    return sorted(str(r["actor"]) for r in rows if r["actor"])


async def recently_diagnosed(db: object, *, now: float | None = None) -> set[str]:
    """Signatures whose RCA is still recent enough to stand — STARTED or finished.

    Both events count. An analysis that began and never returned consumed the same
    ~140,000 tokens as one that finished, and four days of evidence say it will be
    interrupted again; treating "started" as "not to be repeated within the window"
    is what turns an unbounded retry into a bounded one.
    

    FAILS TOWARD DIAGNOSING. An unreadable ledger returns the empty set, so the
    loop behaves exactly as it did before this existed. A suppression that fired
    because a query failed would silently disable the self-heal loop — the failure
    mode this whole arc exists to prevent.
    """
    stamp = time.time() if now is None else now
    since = stamp - _DIAGNOSIS_GOOD_FOR_HOURS * 3600.0
    try:
        rows = await db.fetch_all(  # type: ignore[attr-defined]
            "SELECT DISTINCT actor FROM audit_log WHERE event_type IN (?, ?) "
            "AND timestamp >= ? AND actor IS NOT NULL",
            (DIAGNOSED_EVENT, STARTED_EVENT, since),
        )
    except Exception as exc:  # noqa: BLE001
        log.scheduler.warning(
            "[scheduler] incident_escalation: could not read the diagnosis ledger — "
            "diagnosing as if nothing had been seen before",
            exc_info=exc,
        )
        return set()
    return {str(r["actor"]) for r in rows if r["actor"]}


_MAX_VERDICT_ATTEMPTS = 3


#: How many incidents may get a full RCA in ONE tick. ONE, and the number is
#: load-bearing rather than cautious.
#:
#: ``staged_rca`` reasons explicitly about the ceiling: "worst case here is three
#: stages plus one retry (4 x 240 = 960s), leaving 240s of margin, and a test pins
#: that relationship so the inner budget can never be silently pre-empted by the
#: outer one." That is correct FOR ONE RCA. This handler used to loop over every
#: new incident sequentially, so two in a tick was up to 1920s against a 1200s
#: ceiling — a guaranteed timeout, with the pinned margin never having a chance.
#:
#: MEASURED 2026-08-31 over 6,684 runs: p50 0.3s, p90 13s, max 1198.5s, and 8
#: dispatch timeouts sitting exactly at the ceiling. A handler with a
#: third-of-a-second median does not reach twenty minutes by being slow.
#:
#: AND THE TIMEOUT DOES NOT STOP IT. The scheduler's own comment records that
#: ``asyncio.wait_for`` cancels the awaited coroutine but not the tasks it spawned,
#: so a timed-out RCA keeps running — "RCA complete" logged NINE MINUTES after its
#: own timeout — while the row stays 'running' until reaped at 2400s. The second
#: RCA was not merely late; it ran untracked and blocked detection for up to forty
#: minutes.
#:
#: NOTHING IS LOST. The handler already assumes incidents persist across ticks: it
#: dedupes to one incident/one RCA and its own comment says the NEXT tick retries a
#: persistent one. The tick is every 10 minutes.
MAX_RCA_PER_TICK = 1


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
        memory_bridge: MemoryBridge | None = None,
        # The DURABLE half of the dedup. None keeps the old in-memory-only
        # behaviour exactly, so an unwired handler is byte-identical.
        db: object | None = None,
    ) -> None:
        self._db = db
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
        # "Update memory depending on the reason" — a verified verdict stages a
        # short recall-able fact via the SAME memory bridge conversation turns
        # use, closing the gap where the RCA->skill pipeline authored a
        # learned/*/SKILL.md but never told the memory bridge WHY a capability
        # failed. None -> byte-identical no-op (feature absent).
        self._memory_bridge = memory_bridge
        # Dedupe: signature -> minted incident_id. A signature already here is an
        # OPEN incident (its RCA already ran); later ticks skip it. Cleared when
        # the signature is no longer active so it can re-open later.
        self._open_incidents: dict[str, str] = {}
        # Consecutive RCA attempts for a signature that produced NO verdict. The
        # no-verdict path deliberately does not register the signature so the next
        # tick retries — see the comment at the retry site — but that retry had no
        # ceiling, so a signature whose RCA reliably fails was re-run every tick for
        # ever at ~70,000 input tokens a time. MEASURED: 94 "RCA produced no
        # verdict" events, and incident lanes are 52.8% of all input tokens spent
        # in the last 24h. Cleared on success, so a transient failure costs nothing.
        self._verdict_failures: dict[str, int] = {}
        # Verdicts produced this process, keyed by (capability_class,
        # failure_class) — the exact map Task 7 / FailureOutcomeMiner.mine consume.
        self.verdicts: dict[tuple[str, str], RcaVerdict] = {}

    def _should_retry_verdict(self, signature: str) -> bool:
        """True while this signature may make another verdict-less RCA attempt."""
        return self._verdict_failures.get(signature, 0) < _MAX_VERDICT_ATTEMPTS

    def _record_verdict_failure(self, signature: str) -> bool:
        """Count one verdict-less attempt. Returns True if another may be made."""
        count = self._verdict_failures.get(signature, 0) + 1
        self._verdict_failures[signature] = count
        return count < _MAX_VERDICT_ATTEMPTS

    def _clear_verdict_failures(self, signature: str) -> None:
        """A verdict was produced — the signature has proven it can be diagnosed."""
        self._verdict_failures.pop(signature, None)

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
        # THE DURABLE HALF. `_open_incidents` above is an instance dict and is EMPTY
        # after any restart — and this platform exec-replaces its core on every
        # commit. MEASURED 2026-08-31: the same seven or eight signatures were
        # reported "new" on every tick all day, 126 RCA sessions, 15,724,829 input
        # tokens, 72.3% of ALL spend, 86 of 100 completions concluding
        # verified=False. Reading the ledger here means a diagnosis survives the
        # thing that was erasing it.
        if self._db is not None and new_incidents:
            # INFO, because this is the ONLY evidence that an analysis was killed
            # mid-flight — there is no exit line for a process that was replaced,
            # and production runs at INFO.
            interrupted = await interrupted_diagnoses(
                self._db, now=self._clock_time(),
            )
            if interrupted:
                log.scheduler.info(
                    "[scheduler] incident_escalation: analyses started and never "
                    "finished — killed mid-flight, tokens spent, no verdict",
                    extra={"_fields": {"count": len(interrupted),
                                       "signatures": interrupted[:8]}},
                )
            already = await recently_diagnosed(self._db, now=self._clock_time())
            if already:
                suppressed = [i for i in new_incidents if i.signature in already]
                if suppressed:
                    new_incidents = [
                        i for i in new_incidents if i.signature not in already
                    ]
                    # INFO, because this line is the evidence the 15.7M stopped.
                    log.scheduler.info(
                        "[scheduler] incident_escalation: already diagnosed within "
                        "%.0fh — not re-running the RCA",
                        _DIAGNOSIS_GOOD_FOR_HOURS,
                        extra={"_fields": {
                            "suppressed": len(suppressed),
                            "signatures": sorted(i.signature for i in suppressed)[:8],
                            "still_new": len(new_incidents),
                        }},
                    )

        # ADR-19 — drop incidents whose cause is that the diagnostic engine
        # itself is unreachable, BEFORE opening them. Filtered here rather than
        # inside _resolve_incident so no incident_id is minted, no "no verdict"
        # warning fires, and the signature stays unregistered — the condition is
        # self-resolving and re-detects on its own once the provider returns.
        deferred = [
            inc for inc in new_incidents
            if classify_incident_retryability(inc.failure_class) == "defer"
        ]
        if deferred:
            new_incidents = [inc for inc in new_incidents if inc not in deferred]
            # WARNING, not debug: "we chose not to diagnose" must never be
            # indistinguishable from "there was nothing to diagnose" (ADR-19 I6).
            log.scheduler.warning(
                "[scheduler] incident_escalation: deferring %d incident(s) — the LLM "
                "substrate is unavailable, so an RCA would need the very thing that "
                "is down. Will re-detect when it recovers.",
                len(deferred),
                extra={"_fields": {
                    "deferred": len(deferred),
                    "failure_classes": sorted({i.failure_class for i in deferred}),
                }},
            )

        analyzed = 0
        short_circuited = 0
        # AT MOST ONE RCA PER TICK — see MAX_RCA_PER_TICK. The cap is on the
        # EXPENSIVE incidents only: a `non_retryable` one short-circuits to a
        # fallback verdict without entering the RCA cycle at all, so it costs no
        # stage time and capping it would defer work for nothing. Predicting which
        # is which uses the same classifier _resolve_incident itself uses, so the
        # two can never disagree about what is expensive.
        rca_bound = [
            inc for inc in new_incidents
            if classify_incident_retryability(inc.failure_class) != "non_retryable"
        ]
        if len(rca_bound) > MAX_RCA_PER_TICK:
            keep = set(id(i) for i in rca_bound[:MAX_RCA_PER_TICK])
            deferred_rca = len(rca_bound) - MAX_RCA_PER_TICK
            # The deferred ones are NOT dropped: they stay out of _open_incidents,
            # so the next tick detects them again and takes the next one.
            log.scheduler.info(
                "[scheduler] incident_escalation: %d incident(s) need an RCA; running "
                "%d this tick and re-detecting the rest next tick, so the staged-RCA "
                "budget stays under the dispatch ceiling",
                len(rca_bound), MAX_RCA_PER_TICK,
                extra={"_fields": {
                    "new_incidents": len(new_incidents),
                    "rca_bound": len(rca_bound),
                    "running_rca": MAX_RCA_PER_TICK,
                    "deferred_to_next_tick": deferred_rca,
                }},
            )
            new_incidents = [
                inc for inc in new_incidents
                if classify_incident_retryability(inc.failure_class) == "non_retryable"
                or id(inc) in keep
            ]

        for inc in new_incidents:
            incident_id = f"incident-{uuid.uuid4().hex[:12]}"
            # BEFORE the analysis, not after. A restart during the next ~16
            # minutes kills the RCA with no exit line and no ledger row, and the
            # next tick then starts the identical analysis from zero. See
            # STARTED_EVENT: 85 of 462 analyses died exactly this way.
            if self._db is not None:
                await record_diagnosis_started(
                    self._db, signature=inc.signature, incident_id=incident_id,
                )
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
                self._clear_verdict_failures(inc.signature)
                self._open_incidents[inc.signature] = incident_id
                if self._db is not None:
                    await record_diagnosis(
                        self._db, signature=inc.signature, incident_id=incident_id,
                        verified=bool(getattr(verdict, "verified", False)),
                        now=self._clock_time(),
                    )
                self.verdicts[inc.key] = verdict
                # Task 7 hook — a short-circuited fallback_verdict (ran_rca=False,
                # the non-retryable/deterministic-domain-failure path) is always an
                # "alternative-needed" verdict; a verdict that came out of the full
                # 3-stage RCA (ran_rca=True) is a proposed "fix". This is the exact
                # signal _resolve_incident already computes — no new classification.
                kind: Literal["fix", "alternative"] = "fix" if ran_rca else "alternative"
                await self._consume_verdict(inc, verdict, kind)
            else:
                may_retry = self._record_verdict_failure(inc.signature)
                if may_retry:
                    log.scheduler.warning(
                        "[scheduler] incident_escalation: RCA produced no verdict — "
                        "NOT marking handled, will retry next tick",
                        extra={"_fields": {
                            "incident_id": incident_id, "signature": inc.signature,
                            "attempt": self._verdict_failures[inc.signature],
                            "max_attempts": _MAX_VERDICT_ATTEMPTS,
                        }},
                    )
                else:
                    # THE CEILING. Register the signature so the tick stops
                    # re-running an RCA that has now failed _MAX_VERDICT_ATTEMPTS
                    # times — counting without closing the dedup would leave the
                    # loop exactly as it was. WARNING, not debug: "we stopped
                    # trying to diagnose this" must never be indistinguishable
                    # from "there was nothing to diagnose" (ADR-19 I6).
                    self._open_incidents[inc.signature] = incident_id
                    if self._db is not None:
                        # The ceiling has to be durable too, or a restart hands the
                        # same signature three more failed RCAs at ~125,000 tokens
                        # each — which is most of what today's 15.7M bought.
                        await record_diagnosis(
                            self._db, signature=inc.signature,
                            incident_id=incident_id, verified=None,
                            now=self._clock_time(),
                        )
                    log.scheduler.warning(
                        "[scheduler] incident_escalation: RCA produced no verdict "
                        "%d times for this signature — giving up on it for now "
                        "rather than re-running the analysis every tick",
                        _MAX_VERDICT_ATTEMPTS,
                        extra={"_fields": {
                            "incident_id": incident_id, "signature": inc.signature,
                            "attempts": _MAX_VERDICT_ATTEMPTS,
                        }},
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
            f"analyzed={analyzed} short_circuited={short_circuited} "
            f"deferred={len(deferred)}",
            error=None, duration_ms=duration_ms,
            metadata={
                "active": len(active), "new": len(new_incidents),
                "analyzed": analyzed, "short_circuited": short_circuited,
                "deferred": len(deferred),
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
        # The denominator, read ONCE per gather and over the SAME window the
        # clustering uses, so numerator and denominator can never describe
        # different periods.
        rates = None
        try:
            rates = await self._outcomes.capability_failure_rates(since_epoch=since)
        except AttributeError:
            log.scheduler.warning(
                "[scheduler] incident_escalation: outcome_store has no "
                "capability_failure_rates — escalating on raw counts, which is "
                "the pre-2026-09-01 behaviour and its token cost",
            )
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
            # THE DENOMINATOR GATE. Recurring is necessary and not sufficient:
            # a capability the platform leans on will always recur. It is an
            # incident only when it fails MORE than the platform itself does.
            #
            # FAILS TOWARD DIAGNOSING, deliberately and for the same reason
            # `recently_diagnosed` does: an unreadable store returns no opinion
            # and every cluster escalates exactly as it did before this existed.
            # A gate that silenced the self-heal loop because a query failed
            # would be the failure mode this whole arc exists to prevent.
            if rates is not None:
                z = rates.z_score(cluster.capability_class)
                if z < _ANOMALY_Z:
                    log.scheduler.info(
                        "[scheduler] incident_escalation: capability fails no more "
                        "than the platform does — not an incident",
                        extra={"_fields": {
                            "capability": cluster.capability_class,
                            "failure_class": cluster.failure_class,
                            "failures": rates.failures.get(
                                cluster.capability_class, 0),
                            "turns": rates.turns.get(cluster.capability_class, 0),
                            "pooled_rate": round(rates.pooled_rate(), 4),
                            "z": round(z, 2), "bar": _ANOMALY_Z,
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
        if self._memory_bridge is not None and verdict.verified:
            try:
                from stackowl.memory.models import StagedFact

                await self._memory_bridge.stage(StagedFact(
                    content=(
                        f"Incident: {inc.capability_class} failed with "
                        f"{inc.failure_class} — {verdict.root_cause} "
                        f"Fix/avoidance: {verdict.fix_pattern} "
                        f"(learned skill: {verdict.skill_name})"
                    ),
                    source_type="agent_self",
                    source_ref=inc.signature,
                    confidence=verdict.confidence if verdict.confidence is not None else 0.7,
                    trust="self",
                ))
            except Exception as exc:  # B5 — a memory-write failure must not wedge the tick
                log.scheduler.error(
                    "[scheduler] incident_escalation: memory_bridge.stage failed",
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
