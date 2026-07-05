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
