"""Task 6 — StagedRcaSession: fixed sequential stages, verifier gates on evidence.

Mocks ONLY the owl backend (the AI provider seam) — everything else is real.
Proves: (a) stages run STRICTLY sequentially, evidence threaded forward; (b) a
verifier that confirms the hypothesis yields verified=True; (c) a verifier that
REJECTS yields verified=False (the gate genuinely gates, no rubber-stamp).
"""

from __future__ import annotations

import pytest

from stackowl.parliament.staged_rca import RcaEvidence, RcaOwls, StagedRcaSession
from stackowl.pipeline.backends.base import OrchestratorBackend
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import ResponseChunk


class _ScriptedBackend(OrchestratorBackend):
    """Returns a scripted response per owl_name and records invocation order +
    the exact prompt each stage received (to prove evidence threading)."""

    def __init__(self, scripts: dict[str, str]) -> None:
        self._scripts = scripts
        self.calls: list[tuple[str, str]] = []  # (owl_name, input_text)

    async def run(self, state: PipelineState) -> PipelineState:
        self.calls.append((state.owl_name, state.input_text))
        text = self._scripts.get(state.owl_name, "")
        chunk = ResponseChunk(
            content=text, is_final=True, chunk_index=0,
            trace_id=state.trace_id, owl_name=state.owl_name,
        )
        return state.evolve(responses=(chunk,))


def _evidence() -> RcaEvidence:
    return RcaEvidence(
        incident_id="incident-test-1",
        capability_class="web_knowledge",
        failure_class="ToolExecutionError",
        brief="EVIDENCE_MARKER: 4 web_fetch failures, all ToolExecutionError timeouts.",
        parent_trace_ids=("t1", "t2"),
    )


@pytest.mark.asyncio
async def test_verified_hypothesis_produces_verified_verdict() -> None:
    backend = _ScriptedBackend({
        "rca_gatherer": "BRIEF_MARKER: 4 timeouts on web_fetch.",
        "hypothesis": (
            "SKILL_NAME: web_fetch_timeout_fix\n"
            "DESCRIPTION: Handle recurring web_fetch timeouts.\n"
            "WHEN_TO_USE: When web_fetch times out repeatedly.\n"
            "ROOT_CAUSE: Upstream endpoint is slow; default timeout too low.\n"
            "FIX: Raise timeout and fall back to web_search."
        ),
        "verifier": (
            "VERDICT: VERIFIED\n"
            "CONFIDENCE: 0.8\n"
            "ROOT_CAUSE: Default web_fetch timeout too low for slow endpoint.\n"
            "FIX: Raise timeout; fall back to web_search."
        ),
    })
    session = StagedRcaSession(backend)

    verdict = await session.analyze(_evidence())

    assert verdict is not None
    assert verdict.verified is True
    assert verdict.capability_class == "web_knowledge"
    assert verdict.failure_class == "ToolExecutionError"
    assert verdict.skill_name == "web_fetch_timeout_fix"
    assert "timeout" in verdict.root_cause.lower()
    assert verdict.confidence == pytest.approx(0.8)
    assert verdict.parent_trace_ids == ("t1", "t2")


@pytest.mark.asyncio
async def test_verifier_rejection_gates_the_verdict() -> None:
    """The centerpiece: an unverifiable hypothesis must NOT be rubber-stamped."""
    backend = _ScriptedBackend({
        "rca_gatherer": "BRIEF_MARKER: 4 timeouts on web_fetch.",
        "hypothesis": (
            "SKILL_NAME: wild_guess\n"
            "DESCRIPTION: guess.\n"
            "WHEN_TO_USE: guess.\n"
            "ROOT_CAUSE: The moon phase caused a cosmic ray bit-flip.\n"
            "FIX: Wait for the next full moon."
        ),
        "verifier": (
            "VERDICT: REJECTED\n"
            "CONFIDENCE: 0.1\n"
            "ROOT_CAUSE: The evidence shows timeouts, not bit-flips — unsupported.\n"
            "FIX: none — hypothesis not supported by the evidence."
        ),
    })
    session = StagedRcaSession(backend)

    verdict = await session.analyze(_evidence())

    # Either a verdict flagged unverified, or None — both mean "not authored".
    assert verdict is None or verdict.verified is False


@pytest.mark.asyncio
async def test_stages_are_sequential_and_thread_evidence() -> None:
    """Order MUST be gatherer → hypothesis → verifier, and each later stage's
    prompt must embed the earlier output (staged, not parallel debate)."""
    backend = _ScriptedBackend({
        "rca_gatherer": "BRIEF_MARKER: distilled evidence here.",
        "hypothesis": (
            "SKILL_NAME: x\nDESCRIPTION: d\nWHEN_TO_USE: w\n"
            "ROOT_CAUSE: HYPO_MARKER root cause.\nFIX: HYPO_MARKER fix."
        ),
        "verifier": "VERDICT: VERIFIED\nROOT_CAUSE: r\nFIX: f",
    })
    session = StagedRcaSession(backend, owls=RcaOwls())

    await session.analyze(_evidence())

    owl_order = [c[0] for c in backend.calls]
    assert owl_order == ["rca_gatherer", "hypothesis", "verifier"]

    prompts = {c[0]: c[1] for c in backend.calls}
    # Stage 1 sees the raw evidence marker.
    assert "EVIDENCE_MARKER" in prompts["rca_gatherer"]
    # Stage 2 sees stage-1's distilled brief.
    assert "BRIEF_MARKER" in prompts["hypothesis"]
    # Stage 3 (verifier) sees BOTH the brief AND the hypothesis — it judges
    # against the same evidence, not a peer's confidence.
    assert "BRIEF_MARKER" in prompts["verifier"]
    assert "HYPO_MARKER" in prompts["verifier"]


@pytest.mark.asyncio
async def test_empty_evidence_stage_yields_no_verdict() -> None:
    backend = _ScriptedBackend({"rca_gatherer": "   "})
    session = StagedRcaSession(backend)
    assert await session.analyze(_evidence()) is None


# ---------------------------------------------------------------------------
# DEBT-18 — a timed-out stage must SELF-HEAL, not silently lose the incident.
#
# Measured on the live platform (2026-07-26/27): the per-stage budget was 30s,
# but a single model call on this deployment has a p90 of 93s and a stage is a
# whole agentic turn. The successful-RCA duration distribution was truncated
# exactly at the limit (max 28.5s/stage against a 30s timeout) — the signature
# of a budget cutting into real work. 323 timeouts against 605 verdicts across
# retained logs, degrading to 100% failure on 2026-07-27.
#
# The budget was wrong AND a timeout was fatal: the except returned None, so
# that incident never got a verdict and nothing retried. Bakir, 2026-07-27:
# "i hope we are going to fix self heal logic, not a block or remove that
# logic." These pin the heal, not the silence.
# ---------------------------------------------------------------------------


class _SlowThenFastBackend(OrchestratorBackend):
    """Sleeps past the budget on its first N calls, then answers normally."""

    def __init__(self, scripts: dict[str, str], slow_calls: int, sleep_s: float = 5.0) -> None:
        self._scripts = scripts
        self._slow_calls = slow_calls
        self._sleep_s = sleep_s
        self.calls: list[str] = []

    async def run(self, state: PipelineState) -> PipelineState:
        self.calls.append(state.owl_name)
        if len(self.calls) <= self._slow_calls:
            import asyncio as _a
            await _a.sleep(self._sleep_s)
        text = self._scripts.get(state.owl_name, "")
        chunk = ResponseChunk(
            content=text, is_final=True, chunk_index=0,
            trace_id=state.trace_id, owl_name=state.owl_name,
        )
        return state.evolve(responses=(chunk,))


def _full_scripts() -> dict[str, str]:
    return {
        "rca_gatherer": "BRIEF_MARKER: 4 timeouts on web_fetch.",
        "hypothesis": (
            "SKILL_NAME: web_fetch_timeout_fix\n"
            "DESCRIPTION: Handle recurring web_fetch timeouts.\n"
            "WHEN_TO_USE: When web_fetch times out repeatedly.\n"
            "ROOT_CAUSE: Upstream endpoint is slow; default timeout too low.\n"
            "FIX: Raise timeout and fall back to web_search."
        ),
        "verifier": "VERDICT: VERIFIED\nCONFIDENCE: 0.8\nWHY: Matches the evidence.",
    }


@pytest.mark.asyncio
async def test_a_timed_out_stage_is_retried_and_the_verdict_survives() -> None:
    """The heal: one slow stage must not cost the incident its analysis.

    Standing rule (feedback_always_self_healing): detect failure, reset,
    auto-recover with a BOUNDED retry. Before this, a single slow stage
    discarded the whole run — including the stages that had already succeeded.
    """
    backend = _SlowThenFastBackend(_full_scripts(), slow_calls=1)
    session = StagedRcaSession(backend, per_stage_timeout_s=0.05)

    verdict = await session.analyze(_evidence())

    assert verdict is not None, "one slow stage must not lose the incident"
    assert verdict.verified is True
    # 4 calls: the timed-out gatherer, its retry, then hypothesis and verifier.
    assert len(backend.calls) == 4


@pytest.mark.asyncio
async def test_the_retry_is_bounded_and_gives_up_honestly() -> None:
    """The bound: retry ONCE, not forever. A stage that is genuinely stuck must
    stop, not spin — and must return None rather than a fabricated verdict."""
    backend = _SlowThenFastBackend(_full_scripts(), slow_calls=99)
    session = StagedRcaSession(backend, per_stage_timeout_s=0.05)

    verdict = await session.analyze(_evidence())

    assert verdict is None, "a genuinely stuck stage surfaces honestly"
    assert len(backend.calls) == 2, "exactly one retry — bounded, never a loop"


@pytest.mark.asyncio
async def test_a_healthy_stage_is_never_retried() -> None:
    """The retry must be a RECOVERY path, not an extra call on the happy path —
    every stage is a paid model turn."""
    backend = _SlowThenFastBackend(_full_scripts(), slow_calls=0)
    session = StagedRcaSession(backend, per_stage_timeout_s=30.0)

    verdict = await session.analyze(_evidence())

    assert verdict is not None
    assert len(backend.calls) == 3, "three stages, three calls, no retries"


def test_the_stage_budget_fits_inside_the_scheduler_handler_timeout() -> None:
    """An INVARIANT, not a preference: the inner budget must be strictly
    bounded by the outer one, or the scheduler kills the handler mid-analysis
    and the per-stage retry never gets to run.

    Three stages plus one retry is the worst case a single analyse can spend.
    """
    from stackowl.parliament.staged_rca import DEFAULT_PER_STAGE_TIMEOUT_S
    from stackowl.scheduler.scheduler import _HANDLER_TIMEOUT_SEC

    worst_case = DEFAULT_PER_STAGE_TIMEOUT_S * 4
    assert worst_case < _HANDLER_TIMEOUT_SEC, (
        f"per-stage {DEFAULT_PER_STAGE_TIMEOUT_S}s x4 = {worst_case}s must stay "
        f"under the scheduler's {_HANDLER_TIMEOUT_SEC}s handler timeout"
    )


def test_the_stage_budget_clears_the_measured_call_latency() -> None:
    """Sized from MEASUREMENT, not taste. Single-call p90 on the deployment
    that produced DEBT-18 was ~93s, and a stage is a whole agentic turn that
    may make several calls. A budget under that truncates real work — which is
    precisely what the 30s default did."""
    from stackowl.parliament.staged_rca import DEFAULT_PER_STAGE_TIMEOUT_S

    measured_single_call_p90_s = 93.0
    assert DEFAULT_PER_STAGE_TIMEOUT_S > measured_single_call_p90_s
