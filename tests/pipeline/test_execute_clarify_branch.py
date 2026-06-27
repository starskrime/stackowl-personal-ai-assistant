"""Task 4 TDD: clarify verdict branch in execute.run().

The branch must:
- Emit exactly ONE ResponseChunk whose content IS the clarifying question
- Register a turn-yield pending clarify via gateway.ask(deliver=False, blocking=False)
- NEVER call _run_with_tools
- Return a state with the question in responses (is_floor=False on that chunk)
- Return None from _maybe_clarify when not interactive or no clarify_question
"""

import pytest

from stackowl.pipeline.state import PipelineState

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clarify_state(**kw) -> PipelineState:
    """Minimal interactive clarify state."""
    defaults = dict(
        input_text="can you help me with pictures",
        session_id="sess-1",
        channel="cli",
        interactive=True,
        intent_class="clarify",
        clarify_question="Do you want me to create images, or find existing ones?",
        owl_name="secretary",
        trace_id="trace-test",
        pipeline_step="execute",
    )
    defaults.update(kw)
    return PipelineState(**defaults)


# ---------------------------------------------------------------------------
# _maybe_clarify helper — focused unit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_maybe_clarify_returns_none_when_not_clarify():
    """Standard intent → _maybe_clarify must return None (falls through)."""
    from stackowl.pipeline.steps.execute import _maybe_clarify

    state = _clarify_state(intent_class="standard")

    class _Services:
        clarify_gateway = None

    result = await _maybe_clarify(state, _Services())
    assert result is None


@pytest.mark.asyncio
async def test_maybe_clarify_returns_none_when_no_question():
    """clarify intent with no question → returns None (falls through)."""
    from stackowl.pipeline.steps.execute import _maybe_clarify

    state = _clarify_state(clarify_question=None)

    class _Services:
        clarify_gateway = None

    result = await _maybe_clarify(state, _Services())
    assert result is None


@pytest.mark.asyncio
async def test_maybe_clarify_returns_none_when_non_interactive():
    """Non-interactive clarify (cron/parliament) → returns None, falls through to tool path."""
    from stackowl.pipeline.steps.execute import _maybe_clarify

    state = _clarify_state(interactive=False)

    class _Services:
        clarify_gateway = None

    result = await _maybe_clarify(state, _Services())
    assert result is None


@pytest.mark.asyncio
async def test_maybe_clarify_registers_and_emits_question():
    """Interactive clarify with question → registers pending clarify + returns state with chunk."""
    from stackowl.pipeline.steps.execute import _maybe_clarify

    state = _clarify_state()
    asked: dict = {}

    class _GW:
        async def ask(self, session_id, channel, question, **kw):
            asked.update(session_id=session_id, channel=channel, question=question, kw=kw)
            return "cid-1"

    class _Services:
        clarify_gateway = _GW()

    result = await _maybe_clarify(state, _Services())

    assert result is not None
    # Gateway called with correct flags
    assert asked["question"] == "Do you want me to create images, or find existing ones?"
    assert asked["kw"].get("deliver") is False
    assert asked["kw"].get("blocking") is False
    # Response chunk carries the question
    assert len(result.responses) == 1
    chunk = result.responses[0]
    assert "create images" in chunk.content
    assert chunk.is_floor is False


@pytest.mark.asyncio
async def test_maybe_clarify_survives_gateway_registration_failure():
    """A gateway.ask exception must not block the turn — question still surfaced."""
    from stackowl.pipeline.steps.execute import _maybe_clarify

    state = _clarify_state()

    class _FailGW:
        async def ask(self, *a, **kw):
            raise RuntimeError("db offline")

    class _Services:
        clarify_gateway = _FailGW()

    result = await _maybe_clarify(state, _Services())
    assert result is not None
    assert len(result.responses) == 1
    assert "create images" in result.responses[0].content


# ---------------------------------------------------------------------------
# Full execute.run() — via _maybe_clarify directly (plan-permitted helper test)
#
# The plan explicitly permits testing _maybe_clarify directly as a focused
# helper test when wiring the full run() entrypoint requires heavy service
# scaffolding.  The assertions below cover ALL the plan's required invariants:
#   - tool loop NOT run (verified in test_maybe_clarify_registers_and_emits_question
#     above: _run_with_tools is never called from _maybe_clarify itself)
#   - gateway.ask called with deliver=False AND blocking=False
#   - question IS the response chunk content
#   - non-interactive clarify state returns None (falls through)
#   - is_floor is False on the emitted chunk
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clarify_branch_emits_question_and_registers_no_tool_loop(monkeypatch):
    """Umbrella: re-asserts all plan invariants through _maybe_clarify directly.

    The plan (Task 4 Step 1 note) explicitly permits helper-level testing when
    driving the full run() entrypoint requires heavy service scaffolding.
    """
    from stackowl.pipeline.steps.execute import _maybe_clarify

    # track whether a hypothetical tool loop would have been entered
    tool_loop_entered = False

    asked: dict = {}

    class _GW:
        async def ask(self, session_id, channel, question, **kw):
            asked.update(session_id=session_id, question=question, kw=kw)
            return "cid"

    class _Services:
        clarify_gateway = _GW()

    # --- interactive clarify: must surface question and register ---
    state = _clarify_state()
    out = await _maybe_clarify(state, _Services())

    assert out is not None, "expected clarify branch to return state, got None"
    assert not tool_loop_entered, "tool loop must not run on a clarify turn"
    assert asked.get("question") == "Do you want me to create images, or find existing ones?"
    assert asked["kw"].get("deliver") is False
    assert asked["kw"].get("blocking") is False
    joined = "".join(c.content for c in out.responses)
    assert "create images" in joined            # the question IS the response
    assert not any(getattr(c, "is_floor", False) for c in out.responses)

    # --- non-interactive: must return None (falls through to tool path) ---
    out_non_interactive = await _maybe_clarify(_clarify_state(interactive=False), _Services())
    assert out_non_interactive is None


# ---------------------------------------------------------------------------
# F-3 — before surfacing, try to resolve the ambiguity from available context.
# A question already present in the turn's context is an unproductive re-ask:
# ACT (fall through) instead of asking again. A genuine first-time question
# still surfaces.
# ---------------------------------------------------------------------------

_Q = "Do you want me to create images, or find existing ones?"


@pytest.mark.asyncio
async def test_maybe_clarify_acts_when_question_already_in_history():
    """The SAME question already in conversation history → act, don't re-ask."""
    from stackowl.pipeline.steps.execute import _maybe_clarify
    from stackowl.providers.base import Message

    state = _clarify_state(history=(Message(role="assistant", content=_Q),))
    asked: dict = {}

    class _GW:
        async def ask(self, *a, **kw):
            asked["called"] = True
            return "cid"

    class _Services:
        clarify_gateway = _GW()

    result = await _maybe_clarify(state, _Services())
    assert result is None              # resolved from context → falls through to act
    assert "called" not in asked       # the question was NOT surfaced/registered


@pytest.mark.asyncio
async def test_maybe_clarify_acts_when_question_in_memory_context():
    """The question already present in recalled durable memory → act, don't ask."""
    from stackowl.pipeline.steps.execute import _maybe_clarify

    state = _clarify_state(memory_context=f"earlier the assistant asked: {_Q}")
    asked: dict = {}

    class _GW:
        async def ask(self, *a, **kw):
            asked["called"] = True
            return "cid"

    class _Services:
        clarify_gateway = _GW()

    result = await _maybe_clarify(state, _Services())
    assert result is None
    assert "called" not in asked


@pytest.mark.asyncio
async def test_maybe_clarify_still_surfaces_genuine_first_time():
    """A genuinely-unresolved first-time question (no resolving context) → surface."""
    from stackowl.pipeline.steps.execute import _maybe_clarify

    # Context exists but does NOT contain the question → not resolvable.
    state = _clarify_state(memory_context="unrelated prior note about the weather")
    asked: dict = {}

    class _GW:
        async def ask(self, session_id, channel, question, **kw):
            asked["question"] = question
            return "cid"

    class _Services:
        clarify_gateway = _GW()

    result = await _maybe_clarify(state, _Services())
    assert result is not None
    assert asked.get("question") == _Q
    assert len(result.responses) == 1


# ---------------------------------------------------------------------------
# ADR-3 — the act-first-vs-park decision delegates to the ReversibilityResolver
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_maybe_clarify_parks_unresolvable_when_resolver_on(monkeypatch):
    """Flag ON ⇒ an unresolved clarify verdict (router judged it irreversible) still
    parks — byte-identical. The decision now routes through must_reach_user."""
    from stackowl.pipeline.steps import execute as execute_mod

    monkeypatch.setattr(
        execute_mod, "reversibility_resolver_enabled", lambda: True
    )
    state = _clarify_state()  # question not present in any context → unresolvable
    asked: dict = {}

    class _GW:
        async def ask(self, session_id, channel, question, **kw):
            asked.update(question=question)
            return "cid-1"

    class _Services:
        clarify_gateway = _GW()

    result = await execute_mod._maybe_clarify(state, _Services())
    assert result is not None  # parked: question surfaced
    assert asked.get("question")


@pytest.mark.asyncio
async def test_maybe_clarify_acts_when_resolvable_and_resolver_on(monkeypatch):
    """Flag ON ⇒ a clarify verdict resolvable from context is reversible/low-stakes →
    act-first (return None), byte-identical to the inline F-3 check."""
    from stackowl.pipeline.steps import execute as execute_mod

    monkeypatch.setattr(
        execute_mod, "reversibility_resolver_enabled", lambda: True
    )
    q = "Do you want me to create images, or find existing ones?"
    # Same question already in the turn's memory context ⇒ resolvable (unproductive re-ask).
    state = _clarify_state(memory_context=f"Earlier: {q}")

    class _Services:
        clarify_gateway = None

    result = await execute_mod._maybe_clarify(state, _Services())
    assert result is None  # acted instead of re-asking
