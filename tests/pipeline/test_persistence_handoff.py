"""PA4 — hand-to-better-owl rung of the never-give-up ladder.

Behavioral tests for ``surface_persistence_handoff``: a would-give-up turn first
tries to hand the request to a capability-matched better-fit owl and deliver its
answer; anything short of a real answer falls through to the honest floor.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from stackowl.infra import tool_outcome_ledger as tol
from stackowl.owls.a2a_delegation import A2AResult
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.delivery_gate import surface_persistence_handoff
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import ResponseChunk


class _FakeStore:
    """semantic_recall returns scripted (skill, similarity) pairs in rank order."""

    def __init__(self, skill_names: list[str]) -> None:
        self._skills = [SimpleNamespace(name=n) for n in skill_names]

    async def semantic_recall(self, embedding, *, limit=5, min_similarity=0.0):
        return [(sk, 0.9 - i * 0.1) for i, sk in enumerate(self._skills[:limit])]


class _FakeDb:
    """read_all_skill_ownership reads via fetch_all — return no durable rows so
    ownership resolves purely from the built-in manifest.skills path."""

    async def fetch_all(self, query, params):
        return []


class _FakeDelegator:
    def __init__(self, result: A2AResult) -> None:
        self._result = result
        self.calls: list[tuple[str, str, str]] = []
        self.last_parent_state: PipelineState | None = None

    async def delegate(self, *, from_owl, to_owl, sub_task, parent_state) -> A2AResult:
        self.calls.append((from_owl, to_owl, sub_task))
        self.last_parent_state = parent_state
        return self._result


def _registry() -> OwlRegistry:
    reg = OwlRegistry.with_default_secretary()
    reg.register(
        OwlAgentManifest(
            name="researcher", role="researcher", system_prompt="research",
            model_tier="fast", skills=("web_research",),
        )
    )
    return reg


def _services(*, store, delegator, registry=None) -> StepServices:
    return StepServices(
        skill_store=store,  # type: ignore[arg-type]
        db_pool=_FakeDb(),  # type: ignore[arg-type]
        owl_registry=registry if registry is not None else _registry(),
        a2a_delegator=delegator,  # type: ignore[arg-type]
    )


def _state(*, giveup: bool, depth: int = 0, budget_capped: bool = False) -> PipelineState:
    """A turn whose draft would be a dressed-up give-up (consequential failure in
    the ledger) when ``giveup`` is True; a plain healthy answer otherwise."""
    if giveup:
        tol.record_tool_outcome(name="send_email", action_severity="consequential", success=False)
    return PipelineState(
        trace_id="t", session_id="s", input_text="research the weather and email it",
        channel="cli", owl_name="secretary", pipeline_step="deliver",
        delegation_depth=depth, budget_capped=budget_capped,
        query_embedding=(0.1, 0.2, 0.3),
        responses=(ResponseChunk(content="Here is your draft answer.", is_final=False,
                                 chunk_index=0, trace_id="t", owl_name="secretary"),),
    )


@pytest.mark.asyncio
async def test_healthy_turn_is_unchanged_no_delegation():
    """A non-give-up turn returns byte-identical state and NEVER delegates."""
    token = tol.bind()
    try:
        delegator = _FakeDelegator(A2AResult(status="ok", content="x"))
        s = _state(giveup=False)
        out = await surface_persistence_handoff(s, _services(store=_FakeStore(["web_research"]), delegator=delegator))
        assert out is s            # untouched object — zero overhead path
        assert delegator.calls == []
    finally:
        tol.reset(token)


@pytest.mark.asyncio
async def test_giveup_hands_off_and_replaces_with_child_answer():
    token = tol.bind()
    try:
        delegator = _FakeDelegator(A2AResult(status="ok", content="It is 24C and sunny."))
        s = _state(giveup=True)
        out = await surface_persistence_handoff(s, _services(store=_FakeStore(["web_research"]), delegator=delegator))
        delivered = "".join(c.content for c in out.responses)
        assert "24C and sunny" in delivered
        assert "researcher" in delivered                       # provenance footer
        assert delegator.calls and delegator.calls[0][1] == "researcher"
        assert all(not c.is_floor for c in out.responses)      # a real answer, not a floor
    finally:
        tol.reset(token)


@pytest.mark.asyncio
async def test_no_better_owl_falls_through_to_floor():
    """Only the current owl owns the matched skill → no hand-off, state unchanged."""
    token = tol.bind()
    try:
        # secretary owns the only matched skill → no DIFFERENT owl can take over.
        reg = OwlRegistry.with_default_secretary()
        sec = reg.get("secretary")
        reg.replace(sec.model_copy(update={"skills": ("self_skill",)}))
        delegator = _FakeDelegator(A2AResult(status="ok", content="x"))
        s = _state(giveup=True)
        out = await surface_persistence_handoff(
            s, _services(store=_FakeStore(["self_skill"]), delegator=delegator, registry=reg)
        )
        assert out is s
        assert delegator.calls == []
    finally:
        tol.reset(token)


@pytest.mark.asyncio
async def test_failed_handoff_falls_through_to_floor():
    """Delegator returns a non-ok status → responses untouched (honest fallback)."""
    token = tol.bind()
    try:
        delegator = _FakeDelegator(A2AResult(status="timeout"))
        s = _state(giveup=True)
        out = await surface_persistence_handoff(s, _services(store=_FakeStore(["web_research"]), delegator=delegator))
        assert out is s
        assert delegator.calls and delegator.calls[0][1] == "researcher"  # it tried
    finally:
        tol.reset(token)


@pytest.mark.asyncio
async def test_empty_handoff_falls_through_to_floor():
    token = tol.bind()
    try:
        delegator = _FakeDelegator(A2AResult(status="ok", content="   "))  # blank → no real answer
        s = _state(giveup=True)
        out = await surface_persistence_handoff(s, _services(store=_FakeStore(["web_research"]), delegator=delegator))
        assert out is s
    finally:
        tol.reset(token)


@pytest.mark.asyncio
async def test_depth_gt_zero_never_hands_off():
    """A delegated child (depth>0) must never re-hand-off — recursion guard."""
    token = tol.bind()
    try:
        delegator = _FakeDelegator(A2AResult(status="ok", content="x"))
        s = _state(giveup=True, depth=1)
        out = await surface_persistence_handoff(s, _services(store=_FakeStore(["web_research"]), delegator=delegator))
        assert out is s
        assert delegator.calls == []
    finally:
        tol.reset(token)


@pytest.mark.asyncio
async def test_budget_capped_never_hands_off():
    token = tol.bind()
    try:
        delegator = _FakeDelegator(A2AResult(status="ok", content="x"))
        s = _state(giveup=True, budget_capped=True)
        out = await surface_persistence_handoff(s, _services(store=_FakeStore(["web_research"]), delegator=delegator))
        assert out is s
        assert delegator.calls == []
    finally:
        tol.reset(token)


@pytest.mark.asyncio
async def test_child_parent_state_has_giveup_snapshot_cleared():
    """The hand-off must NOT leak the PARENT's give-up snapshot into the child:
    _run_specialist evolves from parent_state without resetting these, so a child
    inheriting the parent's failed consequential tally would floor on the parent's
    failure and never return ok — defeating the hand-off."""
    token = tol.bind()
    try:
        delegator = _FakeDelegator(A2AResult(status="ok", content="done by researcher"))
        # A give-up turn whose snapshot is stamped ON state (the production shape).
        s = PipelineState(
            trace_id="t", session_id="s", input_text="research and email it",
            channel="cli", owl_name="secretary", pipeline_step="deliver",
            query_embedding=(0.1, 0.2, 0.3),
            consequential_failures=("send_email",),  # parent's failed action
            no_progress_tools=("send_email",),
            turn_made_progress=False,
            responses=(ResponseChunk(content="draft", is_final=False, chunk_index=0,
                                     trace_id="t", owl_name="secretary"),),
        )
        out = await surface_persistence_handoff(
            s, _services(store=_FakeStore(["web_research"]), delegator=delegator)
        )
        assert "done by researcher" in "".join(c.content for c in out.responses)
        ps = delegator.last_parent_state
        assert ps is not None
        # The child starts clean — none of the parent's give-up snapshot carries.
        assert ps.consequential_failures == ()
        assert ps.no_progress_tools == ()
        assert ps.turn_made_progress is True
        assert ps.has_consequential_snapshot is False
    finally:
        tol.reset(token)
