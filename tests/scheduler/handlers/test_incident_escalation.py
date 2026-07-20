"""Task 6 — IncidentEscalationHandler: trigger, dedupe, transient/structural gate.

Proves:
* A subsystem stuck degraded PAST the recycle-retry point (real HealthSweepHandler
  drives its alert-state map) produces exactly ONE RCA session across many ticks,
  not one per tick.
* A KNOWN-non-retryable failure class short-circuits to a fallback verdict WITHOUT
  running the 3-stage RCA.
* A transient/infra failure class DOES run the RCA.
The staged-RCA session itself is faked here (its own gating is covered by
test_staged_rca.py) — this file covers the TRIGGER + dedupe + classification.
"""

from __future__ import annotations

import pytest

from stackowl.health.status import HealthStatus
from stackowl.learning.failure_outcome_miner import RcaVerdict
from stackowl.memory.outcome_store import TaskOutcome
from stackowl.parliament.staged_rca import RcaEvidence
from stackowl.scheduler.handlers import incident_escalation as mod
from stackowl.scheduler.handlers.health_sweep import HealthSweepHandler
from stackowl.scheduler.handlers.incident_escalation import (
    IncidentEscalationHandler,
    classify_incident_retryability,
)
from stackowl.scheduler.job import Job


class _FakeAggregator:
    def __init__(self, statuses: list[HealthStatus]) -> None:
        self._statuses = statuses

    async def collect(self) -> list[HealthStatus]:
        return self._statuses


class _FakeOutcomeStore:
    def __init__(
        self, outcomes: list[TaskOutcome], recovered: list[TaskOutcome] | None = None,
    ) -> None:
        self._outcomes = outcomes
        self._recovered = recovered or []

    async def list_failed_global(self, *, since_epoch: float = 0.0, limit: int = 2000):
        return list(self._outcomes)

    async def list_recovered_global(self, *, since_epoch: float = 0.0, limit: int = 2000):
        return list(self._recovered)


class _RecordingRca:
    """Fake StagedRcaSession — records every analyze() call, returns a verdict."""

    def __init__(self) -> None:
        self.calls: list[RcaEvidence] = []

    async def analyze(self, evidence: RcaEvidence) -> RcaVerdict:
        self.calls.append(evidence)
        return RcaVerdict(
            capability_class=evidence.capability_class,
            failure_class=evidence.failure_class,
            skill_name="learned_fix",
            description="d", when_to_use="w",
            root_cause="rc", fix_pattern="fx", verified=True,
        )


class _FlakyRca:
    """Fake StagedRcaSession — hard-fails (returns None) N times, then succeeds."""

    def __init__(self, fail_times: int) -> None:
        self._fail_times = fail_times
        self.calls: list[RcaEvidence] = []

    async def analyze(self, evidence: RcaEvidence) -> RcaVerdict | None:
        self.calls.append(evidence)
        if len(self.calls) <= self._fail_times:
            return None  # hard failure — e.g. a stage backend/provider error
        return RcaVerdict(
            capability_class=evidence.capability_class,
            failure_class=evidence.failure_class,
            skill_name="learned_fix",
            description="d", when_to_use="w",
            root_cause="rc", fix_pattern="fx", verified=True,
        )


def _outcome(trace: str, failure_class: str, tool: str) -> TaskOutcome:
    return TaskOutcome(
        outcome_id=0, trace_id=trace, session_id="s", owl_name="o",
        channel="cli", success=False, latency_ms=1.0, tool_call_count=1,
        failure_class=failure_class, quality_score=None, step_durations={},
        input_text="do the thing", response_text="", captured_at=0.0,
        scored_at=None, tool_sequence=(tool,),
    )


def _recovered_outcome(trace: str, recovered_via_tool: str) -> TaskOutcome:
    """A turn a substitution BRIDGED — trustworthy SUCCESS, failure_class=NULL,
    invisible to list_failed_global. This is the masked-chronic-outage shape."""
    return TaskOutcome(
        outcome_id=0, trace_id=trace, session_id="s", owl_name="o",
        channel="cli", success=True, latency_ms=1.0, tool_call_count=1,
        failure_class=None, quality_score=None, step_durations={},
        input_text="do the thing", response_text="ok", captured_at=0.0,
        scored_at=None, tool_sequence=(recovered_via_tool,),
        recovered_via_tool=recovered_via_tool,
    )


def _job() -> Job:
    return Job(
        job_id="ie-1", handler_name="incident_escalation",
        schedule="every 10m", idempotency_key="incident_escalation:every-10m",
        last_run_at=None, next_run_at="2026-07-04T00:00:00+00:00", status="pending",
    )


@pytest.fixture(autouse=True)
def _enable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Flag ON for both the sweep's heal loop and the escalation handler."""
    monkeypatch.setattr(mod, "_incident_escalation_enabled", lambda: True)
    from stackowl.scheduler.handlers import health_sweep as hs
    monkeypatch.setattr(hs, "_health_loop_enabled", lambda: True)


# --------------------------------------------------------------------------- #
# Classification is grounded in the REAL exception hierarchy.
# --------------------------------------------------------------------------- #

def test_classification_grounded_in_exception_hierarchy() -> None:
    # InfrastructureError subtree / timeouts → analyze (recurring infra → diagnose)
    assert classify_incident_retryability("ToolExecutionError") == "analyze"
    assert classify_incident_retryability("RateLimitError") == "analyze"
    assert classify_incident_retryability("OwlTimeoutError") == "analyze"
    # DomainError subtree → non_retryable (deterministic → substitute, no RCA)
    assert classify_incident_retryability("ManifestValidationError") == "non_retryable"
    assert classify_incident_retryability("UnsupportedActionError") == "non_retryable"
    assert classify_incident_retryability("ProviderNotFoundError") == "non_retryable"
    # Unknown / health status → analyze (never skip a diagnosis on uncertainty)
    assert classify_incident_retryability("down") == "analyze"
    assert classify_incident_retryability("some_random_string") == "analyze"


# --------------------------------------------------------------------------- #
# ONE incident → ONE RCA session, across many ticks (dedupe).
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_one_incident_one_rca_across_ticks() -> None:
    # A real sweep drives a subsystem that STAYS degraded even after a recycle
    # (the healer no-ops), so its alert-state map records the persistent incident.
    class _NoopHealer:
        async def ensure_available(self) -> None:
            return None

    degraded = [HealthStatus("cache", "degraded", "slow", 5.0)]
    sweep = HealthSweepHandler(
        _FakeAggregator(degraded),  # type: ignore[arg-type]
        healers={"cache": _NoopHealer()},  # type: ignore[dict-item]
    )
    rca = _RecordingRca()
    handler = IncidentEscalationHandler(
        health_sweep=sweep,
        outcome_store=_FakeOutcomeStore([]),  # type: ignore[arg-type]
        rca_session=rca,  # type: ignore[arg-type]
    )

    # Drive several sweep+escalation ticks past the recycle-retry point.
    for _ in range(4):
        await sweep.execute(_job())            # recycle attempted, still degraded
        await handler.execute(_job())          # escalation tick

    # Recycle ran every tick and failed every tick, yet exactly ONE RCA session.
    assert len(rca.calls) == 1
    assert rca.calls[0].capability_class == "cache"
    assert rca.calls[0].failure_class == "degraded"
    assert ("cache", "degraded") in handler.verdicts


# --------------------------------------------------------------------------- #
# Non-retryable failure class short-circuits to fallback (NO 3-stage RCA).
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_nonretryable_short_circuits_without_rca() -> None:
    healthy = HealthSweepHandler(_FakeAggregator([]))  # type: ignore[arg-type]
    # 3 failed outcomes, same capability + a DETERMINISTIC domain failure class.
    outcomes = [
        _outcome("t1", "ManifestValidationError", "some_tool"),
        _outcome("t2", "ManifestValidationError", "some_tool"),
        _outcome("t3", "ManifestValidationError", "some_tool"),
    ]
    rca = _RecordingRca()
    handler = IncidentEscalationHandler(
        health_sweep=healthy,
        outcome_store=_FakeOutcomeStore(outcomes),  # type: ignore[arg-type]
        rca_session=rca,  # type: ignore[arg-type]
    )

    result = await handler.execute(_job())

    # The 3-stage RCA was NOT run for a non-retryable class.
    assert rca.calls == []
    # But a fallback ("alternative needed") verdict WAS produced + stored.
    verdict = handler.verdicts[("some_tool", "ManifestValidationError")]
    assert verdict.verified is True
    assert "alternative" in verdict.fix_pattern.lower() or "substitute" in verdict.fix_pattern.lower()
    assert result.metadata["short_circuited"] == 1
    assert result.metadata["analyzed"] == 0


@pytest.mark.asyncio
async def test_transient_failure_class_runs_rca() -> None:
    healthy = HealthSweepHandler(_FakeAggregator([]))  # type: ignore[arg-type]
    outcomes = [
        _outcome("t1", "ToolExecutionError", "web_fetch"),
        _outcome("t2", "ToolExecutionError", "web_fetch"),
        _outcome("t3", "ToolExecutionError", "web_fetch"),
    ]
    rca = _RecordingRca()
    handler = IncidentEscalationHandler(
        health_sweep=healthy,
        outcome_store=_FakeOutcomeStore(outcomes),  # type: ignore[arg-type]
        rca_session=rca,  # type: ignore[arg-type]
    )

    result = await handler.execute(_job())

    assert len(rca.calls) == 1
    assert rca.calls[0].capability_class == "web_fetch"
    assert result.metadata["analyzed"] == 1
    assert result.metadata["short_circuited"] == 0


def _sprawling_outcome(trace: str, *, tools: tuple[str, ...]) -> TaskOutcome:
    """A long, multi-capability turn whose failure was never pinned on one
    tool (``failed_capability`` stays unset/None) — the shape that produced
    the 2026-07-08 false skill_view incident."""
    return TaskOutcome(
        outcome_id=0, trace_id=trace, session_id="s", owl_name="o",
        channel="cli", success=False, latency_ms=1.0, tool_call_count=len(tools),
        failure_class="unachieved_effect", quality_score=None, step_durations={},
        input_text="do the thing", response_text="", captured_at=0.0,
        scored_at=None, tool_sequence=tools,
    )


@pytest.mark.asyncio
async def test_fake_incident_guard_skips_co_occurrence_only_cluster() -> None:
    """A frequently-called tool that only ever CO-OCCURS in sprawling,
    goal-refuted turns (failed_capability=None every time, paired with a
    DIFFERENT other tool each time) must NOT trigger a full RCA — the
    skill_view false-incident shape (2026-07-08, see
    project_skill_view_false_incident_rejected memory)."""
    healthy = HealthSweepHandler(_FakeAggregator([]))  # type: ignore[arg-type]
    outcomes = [
        _sprawling_outcome("t1", tools=("skill_view", "memory")),
        _sprawling_outcome("t2", tools=("skill_view", "owl_build")),
        _sprawling_outcome("t3", tools=("skill_view", "tool_search")),
    ]
    rca = _RecordingRca()
    handler = IncidentEscalationHandler(
        health_sweep=healthy,
        outcome_store=_FakeOutcomeStore(outcomes),  # type: ignore[arg-type]
        rca_session=rca,  # type: ignore[arg-type]
    )

    result = await handler.execute(_job())

    assert rca.calls == []
    assert ("skill_view", "unachieved_effect") not in handler.verdicts
    assert result.metadata["analyzed"] == 0
    assert result.metadata["short_circuited"] == 0


@pytest.mark.asyncio
async def test_single_tool_turns_still_open_a_real_incident() -> None:
    """The guard must NOT swallow genuine single-capability recurrence: every
    row names exactly one capability (no fan-out ambiguity), so co-occurrence
    IS precise here — this must still open and run the RCA."""
    healthy = HealthSweepHandler(_FakeAggregator([]))  # type: ignore[arg-type]
    outcomes = [
        _outcome("t1", "ToolExecutionError", "web_fetch"),
        _outcome("t2", "ToolExecutionError", "web_fetch"),
        _outcome("t3", "ToolExecutionError", "web_fetch"),
    ]
    rca = _RecordingRca()
    handler = IncidentEscalationHandler(
        health_sweep=healthy,
        outcome_store=_FakeOutcomeStore(outcomes),  # type: ignore[arg-type]
        rca_session=rca,  # type: ignore[arg-type]
    )

    result = await handler.execute(_job())

    assert len(rca.calls) == 1
    assert rca.calls[0].capability_class == "web_fetch"
    assert result.metadata["analyzed"] == 1


def _precise_outcome(trace: str, tool: str, *, failure_class: str = "unachieved_effect") -> TaskOutcome:
    """A row with ``failed_capability`` genuinely pinned — real evidence."""
    return TaskOutcome(
        outcome_id=0, trace_id=trace, session_id="s", owl_name="o",
        channel="cli", success=False, latency_ms=1.0, tool_call_count=1,
        failure_class=failure_class, quality_score=None, step_durations={},
        input_text="do the thing", response_text="", captured_at=0.0,
        scored_at=None, tool_sequence=(tool,), failed_capability=tool,
    )


@pytest.mark.asyncio
async def test_one_real_row_diluted_by_noise_does_not_escalate() -> None:
    """2026-07-08 shell-misattribution incident: a cluster can clear the raw
    min_size (3) with only ONE genuinely-attributed row plus co-occurrence
    noise rows (a DIFFERENT tool actually failed each time, shell just rode
    along in a sprawling multi-tool turn). One real occurrence is not
    "recurring" — must NOT escalate."""
    healthy = HealthSweepHandler(_FakeAggregator([]))  # type: ignore[arg-type]
    outcomes = [
        _precise_outcome("real1", "shell"),
        _sprawling_outcome("noise1", tools=("shell", "owl_build")),
        _sprawling_outcome("noise2", tools=("shell", "skill_manage")),
    ]
    rca = _RecordingRca()
    handler = IncidentEscalationHandler(
        health_sweep=healthy,
        outcome_store=_FakeOutcomeStore(outcomes),  # type: ignore[arg-type]
        rca_session=rca,  # type: ignore[arg-type]
    )

    result = await handler.execute(_job())

    assert rca.calls == []
    assert ("shell", "unachieved_effect") not in handler.verdicts
    assert result.metadata["analyzed"] == 0


@pytest.mark.asyncio
async def test_evidence_excludes_co_occurrence_noise_rows() -> None:
    """When there ARE enough real rows to escalate, the evidence handed to the
    RCA analyzer must contain ONLY the precisely-attributed rows — noise rows
    must not dilute/mislead the root-cause narrative with unrelated traces."""
    healthy = HealthSweepHandler(_FakeAggregator([]))  # type: ignore[arg-type]
    outcomes = [
        _precise_outcome("real1", "shell"),
        _precise_outcome("real2", "shell"),
        _precise_outcome("real3", "shell"),
        _sprawling_outcome("noise1", tools=("shell", "owl_build")),
        _sprawling_outcome("noise2", tools=("shell", "skill_manage")),
    ]
    rca = _RecordingRca()
    handler = IncidentEscalationHandler(
        health_sweep=healthy,
        outcome_store=_FakeOutcomeStore(outcomes),  # type: ignore[arg-type]
        rca_session=rca,  # type: ignore[arg-type]
    )

    await handler.execute(_job())

    assert len(rca.calls) == 1
    evidence = rca.calls[0]
    assert set(evidence.parent_trace_ids) == {"real1", "real2", "real3"}
    assert "noise1" not in evidence.brief
    assert "noise2" not in evidence.brief


# --------------------------------------------------------------------------- #
# Review Finding 1 — masked-recurring-substitution (the arc's central antipattern:
# a permanent fallback with zero retry) must be DETECTED even though every turn
# "worked" and NO failed TaskOutcome row exists for any of them.
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_masked_recurring_substitution_detected_with_no_failed_rows() -> None:
    healthy = HealthSweepHandler(_FakeAggregator([]))  # type: ignore[arg-type]
    # Every one of these turns SUCCEEDED (bridged by substitution) — zero failed
    # rows exist. Only list_recovered_global (migration 0077) can see this.
    recovered = [
        _recovered_outcome("r1", "flaky_api"),
        _recovered_outcome("r2", "flaky_api"),
        _recovered_outcome("r3", "flaky_api"),
    ]
    rca = _RecordingRca()
    handler = IncidentEscalationHandler(
        health_sweep=healthy,
        outcome_store=_FakeOutcomeStore([], recovered=recovered),  # type: ignore[arg-type]
        rca_session=rca,  # type: ignore[arg-type]
    )

    result = await handler.execute(_job())

    # An incident WAS detected and analyzed purely from the recovered-outcome
    # signal — no failed TaskOutcome row was ever needed.
    assert len(rca.calls) == 1
    evidence = rca.calls[0]
    assert evidence.capability_class == "flaky_api"
    assert "bridged" in evidence.brief.lower() or "substitut" in evidence.brief.lower()
    assert result.metadata["analyzed"] == 1
    assert ("flaky_api", mod._MASKED_SUBSTITUTION_FAILURE_CLASS) in handler.verdicts


# --------------------------------------------------------------------------- #
# Review Finding 2 — a hard-failed RCA (verdict=None) must NOT permanently
# suppress future re-analysis of the same persistent incident (zero-retry bug).
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_hard_failed_rca_retries_on_next_tick() -> None:
    class _NoopHealer:
        async def ensure_available(self) -> None:
            return None

    degraded = [HealthStatus("cache", "degraded", "slow", 5.0)]
    sweep = HealthSweepHandler(
        _FakeAggregator(degraded),  # type: ignore[arg-type]
        healers={"cache": _NoopHealer()},  # type: ignore[dict-item]
    )
    # First RCA attempt hard-fails (e.g. a stage backend/provider outage);
    # second attempt succeeds.
    rca = _FlakyRca(fail_times=1)
    handler = IncidentEscalationHandler(
        health_sweep=sweep,
        outcome_store=_FakeOutcomeStore([]),  # type: ignore[arg-type]
        rca_session=rca,  # type: ignore[arg-type]
    )

    await sweep.execute(_job())
    result1 = await handler.execute(_job())   # RCA attempt 1 — hard fails
    assert result1.metadata["analyzed"] == 1  # attempted (ran_rca=True) even though it failed
    assert ("cache", "degraded") not in handler.verdicts  # attempt 1 produced nothing

    await sweep.execute(_job())
    result2 = await handler.execute(_job())   # RCA attempt 2 — must RETRY, not skip
    assert result2.metadata["analyzed"] == 1
    assert ("cache", "degraded") in handler.verdicts  # attempt 2 succeeded and is stored

    assert len(rca.calls) == 2, "a hard-failed RCA must be retried, not permanently suppressed"


# --------------------------------------------------------------------------- #
# Task 7 — the minimal consumption hook: a NEW verdict is routed, alerted, and
# handed to the miner. All three are best-effort (B5): a hook failure must
# never block dedup or the next tick's detection.
# --------------------------------------------------------------------------- #

class _RecordingMiner:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def mine(self, verdicts: dict) -> object:
        self.calls.append(dict(verdicts))

        class _Report:
            n_clusters_found = 0
            n_skills_written = 0

        return _Report()


class _BoomingMiner:
    async def mine(self, verdicts: dict) -> object:
        raise RuntimeError("miner exploded")


@pytest.mark.asyncio
async def test_new_verdict_routed_with_kind_derived_from_ran_rca() -> None:
    """A short-circuited (non-retryable) verdict is "alternative"; a fully
    analyzed verdict is "fix" — exactly the ran_rca signal _resolve_incident
    already computes, no new classification invented."""
    routed: list[tuple[RcaVerdict, str]] = []

    async def _router(verdict: RcaVerdict, kind: str) -> None:
        routed.append((verdict, kind))

    healthy = HealthSweepHandler(_FakeAggregator([]))  # type: ignore[arg-type]
    outcomes = [
        _outcome("t1", "ManifestValidationError", "some_tool"),
        _outcome("t2", "ManifestValidationError", "some_tool"),
        _outcome("t3", "ManifestValidationError", "some_tool"),
        _outcome("t4", "ToolExecutionError", "web_fetch"),
        _outcome("t5", "ToolExecutionError", "web_fetch"),
        _outcome("t6", "ToolExecutionError", "web_fetch"),
    ]
    handler = IncidentEscalationHandler(
        health_sweep=healthy,
        outcome_store=_FakeOutcomeStore(outcomes),  # type: ignore[arg-type]
        rca_session=_RecordingRca(),  # type: ignore[arg-type]
        verdict_router=_router,
    )

    await handler.execute(_job())

    kinds = {(v.capability_class, v.failure_class): k for v, k in routed}
    assert kinds[("some_tool", "ManifestValidationError")] == "alternative"
    assert kinds[("web_fetch", "ToolExecutionError")] == "fix"


@pytest.mark.asyncio
async def test_new_verdict_alerts_with_rca_summary_not_bare_flap() -> None:
    alerts: list[str] = []

    async def _alert(message: str) -> None:
        alerts.append(message)

    healthy = HealthSweepHandler(_FakeAggregator([]))  # type: ignore[arg-type]
    outcomes = [
        _outcome("t1", "ToolExecutionError", "web_fetch"),
        _outcome("t2", "ToolExecutionError", "web_fetch"),
        _outcome("t3", "ToolExecutionError", "web_fetch"),
    ]
    handler = IncidentEscalationHandler(
        health_sweep=healthy,
        outcome_store=_FakeOutcomeStore(outcomes),  # type: ignore[arg-type]
        rca_session=_RecordingRca(),  # type: ignore[arg-type]
        alert=_alert,
    )

    await handler.execute(_job())

    assert len(alerts) == 1
    # The RECORDING RCA's verdict carries root_cause="rc" / fix_pattern="fx" —
    # the alert must carry those, not a bare "down"/"degraded" status flap.
    assert "rc" in alerts[0]
    assert "fx" in alerts[0]


class _RecordingUnverifiedRca:
    """Fake StagedRcaSession that returns a verdict the verifier stage REJECTED
    (verified=False) — the "(unverified)" alert shape operators asked to stop
    seeing in chat."""

    async def analyze(self, evidence: RcaEvidence) -> RcaVerdict:
        return RcaVerdict(
            capability_class=evidence.capability_class,
            failure_class=evidence.failure_class,
            skill_name="learned_fix",
            description="d", when_to_use="w",
            root_cause="rc", fix_pattern="fx", verified=False,
        )


@pytest.mark.asyncio
async def test_unverified_verdict_never_alerts_chat() -> None:
    """A verdict the verifier stage could not confirm must never reach the
    operator chat — only a verified=True verdict (or the always-verified
    fallback_verdict short-circuit) does. Logged, not chat-alerted."""
    alerts: list[str] = []

    async def _alert(message: str) -> None:
        alerts.append(message)

    healthy = HealthSweepHandler(_FakeAggregator([]))  # type: ignore[arg-type]
    outcomes = [
        _outcome("t1", "ToolExecutionError", "web_fetch"),
        _outcome("t2", "ToolExecutionError", "web_fetch"),
        _outcome("t3", "ToolExecutionError", "web_fetch"),
    ]
    handler = IncidentEscalationHandler(
        health_sweep=healthy,
        outcome_store=_FakeOutcomeStore(outcomes),  # type: ignore[arg-type]
        rca_session=_RecordingUnverifiedRca(),  # type: ignore[arg-type]
        alert=_alert,
    )

    await handler.execute(_job())

    assert alerts == []
    # Still marked handled (dedup closed) — an unverified verdict is a real,
    # already-produced RcaVerdict, not a hard RCA failure to retry.
    assert len(handler._open_incidents) == 1


@pytest.mark.asyncio
async def test_new_verdict_feeds_the_miner() -> None:
    miner = _RecordingMiner()
    healthy = HealthSweepHandler(_FakeAggregator([]))  # type: ignore[arg-type]
    outcomes = [
        _outcome("t1", "ToolExecutionError", "web_fetch"),
        _outcome("t2", "ToolExecutionError", "web_fetch"),
        _outcome("t3", "ToolExecutionError", "web_fetch"),
    ]
    handler = IncidentEscalationHandler(
        health_sweep=healthy,
        outcome_store=_FakeOutcomeStore(outcomes),  # type: ignore[arg-type]
        rca_session=_RecordingRca(),  # type: ignore[arg-type]
        miner=miner,  # type: ignore[arg-type]
    )

    await handler.execute(_job())

    assert len(miner.calls) == 1
    # mine() was called with this incident's own verdict — the exact interface
    # Task 5 defined (Mapping[(capability_class, failure_class), RcaVerdict]).
    assert ("web_fetch", "ToolExecutionError") in miner.calls[0]


@pytest.mark.asyncio
async def test_second_new_incident_mines_only_its_own_verdict() -> None:
    """mine() must be called with ONLY the newly-consumed verdict, not the full
    accumulated self.verdicts history. Before this fix, every new incident
    re-passed the whole map, so a tick with N previously-resolved signatures
    already open re-mined all N of them again — wasted work every tick,
    visible as a "skill already exists — skip" line per old signature,
    forever, on every recurring scheduler run.
    """
    miner = _RecordingMiner()
    healthy = HealthSweepHandler(_FakeAggregator([]))  # type: ignore[arg-type]
    handler = IncidentEscalationHandler(
        health_sweep=healthy,
        outcome_store=_FakeOutcomeStore([
            _outcome("t1", "ToolExecutionError", "web_fetch"),
            _outcome("t2", "ToolExecutionError", "web_fetch"),
            _outcome("t3", "ToolExecutionError", "web_fetch"),
        ]),  # type: ignore[arg-type]
        rca_session=_RecordingRca(),  # type: ignore[arg-type]
        miner=miner,  # type: ignore[arg-type]
    )
    await handler.execute(_job())  # tick 1: opens web_fetch/ToolExecutionError
    assert len(miner.calls) == 1

    # tick 2: a SECOND, distinct signature appears. If the old signature is
    # still active, its incident stays open (dedup) and its verdict must NOT
    # be re-passed to mine() this tick.
    handler._outcomes = _FakeOutcomeStore([  # type: ignore[attr-defined]
        _outcome("t1", "ToolExecutionError", "web_fetch"),
        _outcome("t2", "ToolExecutionError", "web_fetch"),
        _outcome("t3", "ToolExecutionError", "web_fetch"),
        _outcome("t4", "ManifestValidationError", "shell"),
        _outcome("t5", "ManifestValidationError", "shell"),
        _outcome("t6", "ManifestValidationError", "shell"),
    ])
    await handler.execute(_job())

    assert len(miner.calls) == 2
    assert ("shell", "ManifestValidationError") in miner.calls[1]
    assert ("web_fetch", "ToolExecutionError") not in miner.calls[1]


@pytest.mark.asyncio
async def test_consumption_hook_failures_never_block_dedup_or_next_tick() -> None:
    """A router/alert/miner that all explode must not stop the incident from
    being marked handled, and must not raise into the scheduler tick."""
    async def _boom_router(verdict: RcaVerdict, kind: str) -> None:
        raise RuntimeError("router exploded")

    async def _boom_alert(message: str) -> None:
        raise RuntimeError("alert exploded")

    healthy = HealthSweepHandler(_FakeAggregator([]))  # type: ignore[arg-type]
    outcomes = [
        _outcome("t1", "ToolExecutionError", "web_fetch"),
        _outcome("t2", "ToolExecutionError", "web_fetch"),
        _outcome("t3", "ToolExecutionError", "web_fetch"),
    ]
    handler = IncidentEscalationHandler(
        health_sweep=healthy,
        outcome_store=_FakeOutcomeStore(outcomes),  # type: ignore[arg-type]
        rca_session=_RecordingRca(),  # type: ignore[arg-type]
        verdict_router=_boom_router,
        miner=_BoomingMiner(),  # type: ignore[arg-type]
        alert=_boom_alert,
    )

    result = await handler.execute(_job())  # must not raise

    assert result.success is True
    assert ("web_fetch", "ToolExecutionError") in handler.verdicts
    # Dedup still closed the incident despite every hook exploding.
    assert len(handler._open_incidents) == 1


class _RecordingBridge:
    """Fake MemoryBridge — records every staged fact."""

    def __init__(self) -> None:
        self.staged: list[object] = []

    async def stage(self, fact: object) -> None:
        self.staged.append(fact)


@pytest.mark.asyncio
async def test_verified_verdict_stages_a_memory_fact() -> None:
    bridge = _RecordingBridge()
    healthy = HealthSweepHandler(_FakeAggregator([]))  # type: ignore[arg-type]
    outcomes = [
        _outcome("t1", "ToolExecutionError", "web_fetch"),
        _outcome("t2", "ToolExecutionError", "web_fetch"),
        _outcome("t3", "ToolExecutionError", "web_fetch"),
    ]
    handler = IncidentEscalationHandler(
        health_sweep=healthy,
        outcome_store=_FakeOutcomeStore(outcomes),  # type: ignore[arg-type]
        rca_session=_RecordingRca(),  # type: ignore[arg-type]
        memory_bridge=bridge,  # type: ignore[arg-type]
    )

    await handler.execute(_job())

    assert len(bridge.staged) == 1
    fact = bridge.staged[0]
    assert fact.source_type == "agent_self"
    assert fact.trust == "self"
    assert "web_fetch" in fact.content
    assert "ToolExecutionError" in fact.content


@pytest.mark.asyncio
async def test_unverified_verdict_does_not_stage_a_memory_fact() -> None:
    """Unverified is exactly the noise operators asked NOT to see (mirrors the
    alert-suppression behavior right above it) — no memory fact either."""
    class _UnverifiedRca:
        async def analyze(self, evidence: RcaEvidence) -> RcaVerdict:
            return RcaVerdict(
                capability_class=evidence.capability_class,
                failure_class=evidence.failure_class,
                skill_name="learned_fix", description="d", when_to_use="w",
                root_cause="rc", fix_pattern="fx", verified=False,
            )

    bridge = _RecordingBridge()
    healthy = HealthSweepHandler(_FakeAggregator([]))  # type: ignore[arg-type]
    outcomes = [
        _outcome("t1", "ToolExecutionError", "web_fetch"),
        _outcome("t2", "ToolExecutionError", "web_fetch"),
        _outcome("t3", "ToolExecutionError", "web_fetch"),
    ]
    handler = IncidentEscalationHandler(
        health_sweep=healthy,
        outcome_store=_FakeOutcomeStore(outcomes),  # type: ignore[arg-type]
        rca_session=_UnverifiedRca(),  # type: ignore[arg-type]
        memory_bridge=bridge,  # type: ignore[arg-type]
    )

    await handler.execute(_job())

    assert bridge.staged == []


@pytest.mark.asyncio
async def test_memory_bridge_stage_failure_never_blocks_dedup_or_next_tick() -> None:
    class _BoomingBridge:
        async def stage(self, fact: object) -> None:
            raise RuntimeError("bridge exploded")

    healthy = HealthSweepHandler(_FakeAggregator([]))  # type: ignore[arg-type]
    outcomes = [
        _outcome("t1", "ToolExecutionError", "web_fetch"),
        _outcome("t2", "ToolExecutionError", "web_fetch"),
        _outcome("t3", "ToolExecutionError", "web_fetch"),
    ]
    handler = IncidentEscalationHandler(
        health_sweep=healthy,
        outcome_store=_FakeOutcomeStore(outcomes),  # type: ignore[arg-type]
        rca_session=_RecordingRca(),  # type: ignore[arg-type]
        memory_bridge=_BoomingBridge(),  # type: ignore[arg-type]
    )

    result = await handler.execute(_job())  # must not raise

    assert result.success is True
    assert len(handler._open_incidents) == 1
