"""ESC-59 — every pipeline lane carries a conversation_id, so the D01.1 freeze applies.

WHY THIS EXISTS. `assemble.run()` reads the frozen system prompt only when
`state.conversation_id` is truthy (pipeline/steps/assemble.py:133) — an empty
stamp means the prompt cache is never even consulted and the turn cold-builds
unconditionally. Measured 2026-08-29 over the retained logs: 2,427 of 2,981
assembles (81.4%) carried an empty stamp, and every one of them cold-built. Where
the stamp was present the cache worked (236 of 498 cached).

The cause was that NINE of twelve PipelineState construction sites never set it,
because the only resolver lived as a closure inside the gateway
(`startup/orchestrator.py`) and took an `IngressMessage` no machine lane has.

The sharpest instance is the one migration 0102 named as its own motivating
example: "the staged RCA drives three owls (rca_gatherer, hypothesis, verifier)
against ONE incident session_key". That is precisely the shape the
(session_key, owl_name) key was designed for — and staged_rca never stamped an
incarnation, so the case D01.1 was built around had never once hit the cache.

These tests pin the INVARIANT rather than the mechanism: a lane that runs more
than one turn must present the same incarnation on every one of them, so the
freeze can key on it. They do NOT assert a particular id format — that is each
caller's business.
"""

from __future__ import annotations

import pytest

from stackowl.parliament.staged_rca import RcaEvidence, RcaOwls, StagedRcaSession
from stackowl.pipeline.backends.base import OrchestratorBackend
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import ResponseChunk


class _RecordingBackend(OrchestratorBackend):
    """Mocks ONLY the provider seam; records the state every stage was given."""

    def __init__(self, scripts: dict[str, str]) -> None:
        self._scripts = scripts
        self.states: list[PipelineState] = []

    async def run(self, state: PipelineState) -> PipelineState:
        self.states.append(state)
        chunk = ResponseChunk(
            content=self._scripts.get(state.owl_name, "ok"),
            is_final=True,
            chunk_index=0,
            trace_id=state.trace_id,
            owl_name=state.owl_name,
        )
        return state.evolve(responses=(chunk,))


def _evidence() -> RcaEvidence:
    return RcaEvidence(
        incident_id="incident-esc59",
        capability_class="web_knowledge",
        failure_class="ToolExecutionError",
        brief="EVIDENCE: 4 web_fetch timeouts.",
        parent_trace_ids=("t1",),
    )


@pytest.mark.asyncio
async def test_staged_rca_stamps_an_incarnation_on_every_stage() -> None:
    """The case migration 0102 named, and the one that never hit the cache.

    Without a stamp assemble.py:133 short-circuits and all three stages cold-build
    against one lane — which is exactly what 872 incident assembles did, 0 cached.
    """
    backend = _RecordingBackend({
        "rca_gatherer": "BRIEF: 4 timeouts.",
        "hypothesis": "HYPOTHESIS: upstream DNS.",
        "verifier": "VERDICT: verified",
    })
    session = StagedRcaSession(
        backend=backend,
        owls=RcaOwls(gatherer="rca_gatherer", hypothesis="hypothesis", verifier="verifier"),
    )
    await session.analyze(_evidence())

    assert backend.states, "no stage ran — the fixture cannot show the bug"
    missing = [s.owl_name for s in backend.states if not s.conversation_id]
    assert not missing, (
        f"stages ran with NO conversation_id: {missing}. assemble.py:133 will skip "
        "the prompt cache entirely for each of them."
    )


@pytest.mark.asyncio
async def test_staged_rca_uses_ONE_incarnation_for_the_whole_run() -> None:
    """Stability, not just presence.

    A fresh id per stage would satisfy 'non-empty' and still never hit the cache,
    because the stored row is matched on the stamp. Three owls on one lane must
    share one incarnation for the (session_key, owl_name) key to pay off.
    """
    backend = _RecordingBackend({
        "rca_gatherer": "BRIEF: x.", "hypothesis": "HYPOTHESIS: y.", "verifier": "VERDICT: verified",
    })
    session = StagedRcaSession(
        backend=backend,
        owls=RcaOwls(gatherer="rca_gatherer", hypothesis="hypothesis", verifier="verifier"),
    )
    await session.analyze(_evidence())

    incarnations = {s.conversation_id for s in backend.states}
    # NOT-VACUOUS GUARD. Written first as `len(...) == 1` and it PASSED on the
    # broken code, because every stage carried "" and {""} is one value. A
    # stability assertion that a total absence satisfies is not an assertion.
    assert incarnations != {""}, "every stage carried an EMPTY stamp — vacuous pass"
    assert len(incarnations) == 1, (
        f"one RCA run presented {len(incarnations)} incarnations {incarnations} — "
        "a per-stage id defeats the freeze exactly as an empty one does."
    )


@pytest.mark.asyncio
async def test_a_correction_retry_stays_in_the_conversation_it_corrects() -> None:
    """A correction retry is the SAME conversation as the turn it corrects.

    Minting a new incarnation here would cold-build a prompt the original turn had
    already frozen, on the very lane most likely to still hold it. Driven through
    the real ``run_corrective`` rather than a helper, so it tests the path
    production takes.
    """
    from unittest.mock import AsyncMock, MagicMock

    from stackowl.pipeline.retry_actuator import RetryActuator

    backend = _RecordingBackend({"secretary": "a corrected answer with a source."})
    actuator = RetryActuator(
        backend=backend,
        channel_registry=MagicMock(),
        retry_store=AsyncMock(),
    )
    original = PipelineState(
        trace_id="t-orig",
        session_key="owl:secretary:telegram:dm:1",
        conversation_id="20260829_120000_abcd",
        input_text="what is the weather",
        channel="telegram",
        owl_name="secretary",
        pipeline_step="start",
    )
    await actuator.run_corrective(original=original, correction="it named no source")

    assert backend.states, "the corrective re-run never reached the backend"
    assert backend.states[0].conversation_id == original.conversation_id, (
        "the retry left its parent's conversation — the frozen prompt is keyed on "
        "the stamp, so this silently forfeits the cache on a warm lane."
    )
