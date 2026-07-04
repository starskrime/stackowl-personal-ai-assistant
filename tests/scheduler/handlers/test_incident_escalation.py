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
    def __init__(self, outcomes: list[TaskOutcome]) -> None:
        self._outcomes = outcomes

    async def list_failed_global(self, *, since_epoch: float = 0.0, limit: int = 2000):
        return list(self._outcomes)


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


def _outcome(trace: str, failure_class: str, tool: str) -> TaskOutcome:
    return TaskOutcome(
        outcome_id=0, trace_id=trace, session_id="s", owl_name="o",
        channel="cli", success=False, latency_ms=1.0, tool_call_count=1,
        failure_class=failure_class, quality_score=None, step_durations={},
        input_text="do the thing", response_text="", captured_at=0.0,
        scored_at=None, tool_sequence=(tool,),
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
