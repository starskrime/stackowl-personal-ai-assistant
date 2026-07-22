"""classify: every intent class gathers the full memory/skills/lessons context.

Owner decision 2026-07-22: the old "lean" gate skipped lessons/skills/graph
context entirely for any turn the router classified "conversational" — but
that class is coarser than "greeting/small-talk" (e.g. "who is online?" and
"what about other agents?" land there too), so a substantive question got the
same stripped-down memory a bare "hey" gets. This locks in the fix: EVERY
intent class now invokes _gather_lessons, _gather_relevant_skills, and
_gather_graph_context.

_gather_recent_actions is a SEPARATE, unrelated gate
(_should_surface_failure_history: intent_class == "standard" and
intent_classified) — deliberately still fail-closed for an unclassified/
non-standard turn, so it is NOT expected to fire for the conversational case
here. See its own docstring for why.

FR-3 (de-complication PRD): reflections are surfaced once per turn via
_gather_lessons (lessons_index) only — _gather_recent_reflections is no
longer invoked from classify.run, so it is not tracked here.
"""

from __future__ import annotations

import pytest

from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps import classify


def _state(intent: str) -> PipelineState:
    return PipelineState(
        trace_id="t",
        session_id="s",
        input_text="hi",
        channel="cli",
        owl_name="secretary",
        pipeline_step="classify",
        intent_class=intent,  # type: ignore[arg-type]
    )


class _StubBridge:
    """Minimal MemoryBridge: no long-term facts, no history."""

    async def retrieve(self, query: str, session_id: str) -> str:  # noqa: ARG002
        return ""

    async def recent_conversation_turns(self, session_id: str, limit: int):  # noqa: ARG002
        return []


@pytest.mark.asyncio
async def test_conversational_still_gathers_lessons_skills_graph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A conversational-classified turn must NOT skip lessons/skills/graph —
    only the separately-gated actions_block stays withheld (unclassified)."""
    called: dict[str, bool] = {
        "lessons": False,
        "skills": False,
        "graph": False,
        "actions": False,
    }

    async def _mark_lessons(*a: object, **k: object) -> str:
        called["lessons"] = True
        return ""

    async def _mark_skills(*a: object, **k: object) -> str:
        called["skills"] = True
        return ""

    async def _mark_graph(*a: object, **k: object) -> str:
        called["graph"] = True
        return ""

    async def _mark_actions(*a: object, **k: object) -> str:
        called["actions"] = True
        return ""

    monkeypatch.setattr(classify, "_gather_lessons", _mark_lessons)
    monkeypatch.setattr(classify, "_gather_relevant_skills", _mark_skills)
    monkeypatch.setattr(classify, "_gather_graph_context", _mark_graph)
    monkeypatch.setattr(classify, "_gather_recent_actions", _mark_actions)

    token = set_services(StepServices(memory_bridge=_StubBridge()))
    try:
        out = await classify.run(_state("conversational"))
    finally:
        reset_services(token)

    assert called["lessons"] is True, "lessons MUST be called for conversational now"
    assert called["skills"] is True, "skills MUST be called for conversational now"
    assert called["graph"] is True, "graph MUST be called for conversational now"
    # actions_block is gated by _should_surface_failure_history (a SEPARATE,
    # unrelated fail-closed check on intent_classified), not by intent_class
    # alone — this turn is not intent_classified, so it stays withheld.
    assert called["actions"] is False, "actions stays fail-closed (unrelated gate)"
    assert out is not None


@pytest.mark.asyncio
async def test_standard_still_gathers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Standard turns (the default) must still invoke every heavy gather."""
    called: dict[str, bool] = {
        "lessons": False,
        "skills": False,
        "graph": False,
        "actions": False,
    }

    async def _yes_lessons(*a: object, **k: object) -> str:
        called["lessons"] = True
        return ""

    async def _yes_skills(*a: object, **k: object) -> str:
        called["skills"] = True
        return ""

    async def _yes_graph(*a: object, **k: object) -> str:
        called["graph"] = True
        return ""

    async def _yes_actions(*a: object, **k: object) -> str:
        called["actions"] = True
        return ""

    monkeypatch.setattr(classify, "_gather_lessons", _yes_lessons)
    monkeypatch.setattr(classify, "_gather_relevant_skills", _yes_skills)
    monkeypatch.setattr(classify, "_gather_graph_context", _yes_graph)
    monkeypatch.setattr(classify, "_gather_recent_actions", _yes_actions)

    # A positively-classified standard work turn must have intent_classified=True;
    # without it the fail-closed gate suppresses the failure-history blocks.
    classified_standard = PipelineState(
        trace_id="t",
        session_id="s",
        input_text="hi",
        channel="cli",
        owl_name="secretary",
        pipeline_step="classify",
        intent_class="standard",
        intent_classified=True,
    )
    token = set_services(StepServices(memory_bridge=_StubBridge()))
    try:
        out = await classify.run(classified_standard)
    finally:
        reset_services(token)

    assert called["lessons"] is True, "lessons MUST be called for standard"
    assert called["skills"] is True, "skills MUST be called for standard"
    assert called["graph"] is True, "graph MUST be called for standard"
    assert called["actions"] is True, "actions MUST be called for standard"
    assert out is not None
