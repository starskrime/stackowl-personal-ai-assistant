"""classify: conversational turns skip heavy memory/skills/lessons blocks.

Intent: a greeting or small-talk turn marked intent_class="conversational"
must NOT invoke _gather_lessons, _gather_relevant_skills,
_gather_recent_reflections, _gather_graph_context, or _gather_recent_actions.
A standard turn (the default) must still invoke every heavy gather.
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
async def test_conversational_skips_heavy_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    """All five heavy gathers must be bypassed for a conversational turn."""
    called: dict[str, bool] = {
        "lessons": False,
        "skills": False,
        "reflections": False,
        "graph": False,
        "actions": False,
    }

    async def _no_lessons(*a: object, **k: object) -> str:
        called["lessons"] = True
        return ""

    async def _no_skills(*a: object, **k: object) -> str:
        called["skills"] = True
        return ""

    async def _no_reflections(*a: object, **k: object) -> str:
        called["reflections"] = True
        return ""

    async def _no_graph(*a: object, **k: object) -> str:
        called["graph"] = True
        return ""

    async def _no_actions(*a: object, **k: object) -> str:
        called["actions"] = True
        return ""

    monkeypatch.setattr(classify, "_gather_lessons", _no_lessons)
    monkeypatch.setattr(classify, "_gather_relevant_skills", _no_skills)
    monkeypatch.setattr(classify, "_gather_recent_reflections", _no_reflections)
    monkeypatch.setattr(classify, "_gather_graph_context", _no_graph)
    monkeypatch.setattr(classify, "_gather_recent_actions", _no_actions)

    token = set_services(StepServices(memory_bridge=_StubBridge()))
    try:
        out = await classify.run(_state("conversational"))
    finally:
        reset_services(token)

    assert called["lessons"] is False, "lessons must NOT be called for conversational"
    assert called["skills"] is False, "skills must NOT be called for conversational"
    assert called["reflections"] is False, "reflections must NOT be called for conversational"
    assert called["graph"] is False, "graph must NOT be called for conversational"
    assert called["actions"] is False, "actions must NOT be called for conversational"
    # State must still be returned — just with minimal/no memory context.
    assert out is not None


@pytest.mark.asyncio
async def test_standard_still_gathers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Standard turns (the default) must still invoke every heavy gather."""
    called: dict[str, bool] = {
        "lessons": False,
        "skills": False,
        "reflections": False,
        "graph": False,
        "actions": False,
    }

    async def _yes_lessons(*a: object, **k: object) -> str:
        called["lessons"] = True
        return ""

    async def _yes_skills(*a: object, **k: object) -> str:
        called["skills"] = True
        return ""

    async def _yes_reflections(*a: object, **k: object) -> str:
        called["reflections"] = True
        return ""

    async def _yes_graph(*a: object, **k: object) -> str:
        called["graph"] = True
        return ""

    async def _yes_actions(*a: object, **k: object) -> str:
        called["actions"] = True
        return ""

    monkeypatch.setattr(classify, "_gather_lessons", _yes_lessons)
    monkeypatch.setattr(classify, "_gather_relevant_skills", _yes_skills)
    monkeypatch.setattr(classify, "_gather_recent_reflections", _yes_reflections)
    monkeypatch.setattr(classify, "_gather_graph_context", _yes_graph)
    monkeypatch.setattr(classify, "_gather_recent_actions", _yes_actions)

    token = set_services(StepServices(memory_bridge=_StubBridge()))
    try:
        out = await classify.run(_state("standard"))
    finally:
        reset_services(token)

    assert called["lessons"] is True, "lessons MUST be called for standard"
    assert called["skills"] is True, "skills MUST be called for standard"
    assert called["reflections"] is True, "reflections MUST be called for standard"
    assert called["graph"] is True, "graph MUST be called for standard"
    assert called["actions"] is True, "actions MUST be called for standard"
    assert out is not None
