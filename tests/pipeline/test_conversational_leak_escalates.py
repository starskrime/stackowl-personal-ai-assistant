"""A tool-call leak on the no-tools conversational path must ESCALATE, not floor.

OBSERVED LIVE 2026-08-10, four turns from Telegram in twenty minutes:

    user: "Hey"                          -> answered normally
    user: "What is happening in world ?" -> NOTHING, twice
    user: "Why?"                         -> NOTHING

The chain, measured from ~/.stackowl/logs/stackowl.jsonl and the messages
table rather than reasoned about:

  1. the router classifies "What is happening in world ?" as `conversational`
  2. execute.run()'s `_use_tools` excludes `conversational` BY DESIGN, so the
     turn is given zero tools — the comment there is load-bearing: it exists
     so a small/weak model cannot spiral into a tool loop
  3. the model tries to search anyway and emits the call as plain text
  4. the leak guard correctly refuses to show the user raw `ACTION:` text
  5. ...and then the turn simply DIED. `persist_turn` logged "floored turn —
     persisting user utterance only (no draft)" and the user got an apology.

Step 4 is right and stays. Step 5 is the defect: the leak is the single most
precise signal available that the ROUTER was wrong about this turn — the model
just told us it needed a tool — and the pipeline threw that signal away instead
of acting on it. Flooring a turn we know how to answer is the give-up
antipattern, not a safety property.

So: escalate ONCE onto the tool-capable path. The anti-spiral protection in (2)
survives, because this is a single re-run driven by evidence, not a standing
grant of tools to conversational turns.

The escalation DECISION is what these tests pin down. They deliberately do not
re-drive `_run_with_tools` itself — it owns steering, the turn registry and
live progress, and has its own tests; a hand-built double for it here would be
the "test double that stopped resembling the real thing" failure this codebase
keeps finding.
"""

from __future__ import annotations

from typing import Any

import pytest


class _LeakyStreamProvider:
    """Streams a final answer that IS an unparsed tool call.

    The text is the real shape seen in production, not an invention:
    `_ACTION_RE` in providers/_react.py matches `^[ \\t>*-]*ACTION:[ \\t]*(name)`.
    """

    name = "leaky-stream"
    protocol = "openai"

    def __init__(self) -> None:
        self.stream_calls = 0

    async def stream(  # noqa: ANN201
        self,
        messages: list[Any],
        model: str,
        **kwargs: object,
    ):
        self.stream_calls += 1
        yield "ACTION: web_search\n"
        yield '```json\n{"query": "world news today"}\n```'


class _FakeProviderRegistry:
    def __init__(self, provider: _LeakyStreamProvider) -> None:
        self._p = provider

    def get(self, name: str) -> _LeakyStreamProvider:
        return self._p

    def get_by_tier(self, tier: str) -> _LeakyStreamProvider:
        return self._p

    def get_with_cascade(self, preferred_tier: str) -> _LeakyStreamProvider:
        return self._p


class _FakeToolRegistry:
    """Minimal stand-in for the `tool_registry is not None and .all()` gate."""

    def all(self) -> list[str]:
        return ["web_search"]


def _state(intent: str = "conversational"):
    from stackowl.pipeline.state import PipelineState

    return PipelineState(
        trace_id="t-leak",
        session_key="s",
        input_text="What is happening in world ?",
        channel="telegram",
        owl_name="secretary",
        pipeline_step="execute",
        intent_class=intent,  # type: ignore[arg-type]
        system_prompt="You are a helper.",
    )


@pytest.mark.asyncio
async def test_a_leak_with_tools_available_escalates_instead_of_flooring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The turn the user actually sent. It must reach the tool path."""
    from stackowl.owls.registry import OwlRegistry
    from stackowl.pipeline.services import StepServices, reset_services, set_services
    from stackowl.pipeline.steps import execute as exe

    provider = _LeakyStreamProvider()
    escalated: dict[str, Any] = {}

    async def _fake_run_with_tools(state, choice, tool_registry):  # noqa: ANN001, ANN202
        escalated["called"] = True
        escalated["intent"] = state.intent_class
        from stackowl.pipeline.streaming import ResponseChunk

        return state.evolve(
            responses=(
                ResponseChunk(
                    content="Here is what is happening in the world.",
                    is_final=True,
                    chunk_index=0,
                    trace_id=state.trace_id,
                    owl_name=state.owl_name,
                ),
            ),
        )

    monkeypatch.setattr(exe, "_run_with_tools", _fake_run_with_tools)

    services = StepServices(
        provider_registry=_FakeProviderRegistry(provider),  # type: ignore[arg-type]
        tool_registry=_FakeToolRegistry(),  # type: ignore[arg-type]
        owl_registry=OwlRegistry.with_default_secretary(),
    )

    stoken = set_services(services)
    try:
        out = await exe.run(_state())
    finally:
        reset_services(stoken)

    assert escalated.get("called"), (
        "a leaked tool call on the conversational path must escalate to the tool "
        f"path; instead the turn ended with step_errors={out.step_errors}"
    )
    assert not any(se.exc_type == "ToolCallLeakError" for se in out.step_errors), (
        f"the escalated turn must not still be filed as a leak: {out.step_errors}"
    )
    assert out.responses, "the user must get an answer, not silence"


@pytest.mark.asyncio
async def test_the_raw_leaked_text_is_never_delivered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Escalating must not become a way for the raw ACTION: text to reach the
    user — that is the property the original guard exists to protect."""
    from stackowl.owls.registry import OwlRegistry
    from stackowl.pipeline.services import StepServices, reset_services, set_services
    from stackowl.pipeline.steps import execute as exe

    async def _fake_run_with_tools(state, choice, tool_registry):  # noqa: ANN001, ANN202
        return state.evolve()

    monkeypatch.setattr(exe, "_run_with_tools", _fake_run_with_tools)

    services = StepServices(
        provider_registry=_FakeProviderRegistry(_LeakyStreamProvider()),  # type: ignore[arg-type]
        tool_registry=_FakeToolRegistry(),  # type: ignore[arg-type]
        owl_registry=OwlRegistry.with_default_secretary(),
    )

    stoken = set_services(services)
    try:
        out = await exe.run(_state())
    finally:
        reset_services(stoken)

    delivered = "".join(getattr(c, "content", "") for c in out.responses)
    assert "ACTION:" not in delivered, f"raw tool-call text leaked: {delivered!r}"


@pytest.mark.asyncio
async def test_escalating_evicts_the_stale_sticky_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Escalation must take over the eviction that flooring used to perform.

    turn_persist.py evicts the sticky route on a FLOORED turn, so the next short
    message gets a real router call instead of inheriting a wrong
    "conversational" classification. A successful escalation means the turn no
    longer floors — so that actuator is never reached. Without evicting here,
    the stale entry survives its full TTL and every short follow-up leaks and
    escalates again: right answers, a wasted stream each time. This is the
    "when you remove a writer, ask what was bounding the thing it wrote to"
    shape, one layer along.
    """
    from stackowl.owls.registry import OwlRegistry
    from stackowl.pipeline.services import StepServices, reset_services, set_services
    from stackowl.pipeline.steps import execute as exe

    evicted: list[str] = []

    class _RecordingStickyCache:
        def evict(self, session_key: str) -> None:
            evicted.append(session_key)

    async def _fake_run_with_tools(state, choice, tool_registry):  # noqa: ANN001, ANN202
        return state.evolve()

    monkeypatch.setattr(exe, "_run_with_tools", _fake_run_with_tools)

    services = StepServices(
        provider_registry=_FakeProviderRegistry(_LeakyStreamProvider()),  # type: ignore[arg-type]
        tool_registry=_FakeToolRegistry(),  # type: ignore[arg-type]
        owl_registry=OwlRegistry.with_default_secretary(),
        sticky_route_cache=_RecordingStickyCache(),  # type: ignore[arg-type]
    )

    stoken = set_services(services)
    try:
        await exe.run(_state())
    finally:
        reset_services(stoken)

    assert evicted == ["s"], (
        "the stale conversational sticky route must be evicted when a leak "
        f"escalates, or the next short message re-leaks; got {evicted}"
    )


@pytest.mark.asyncio
async def test_with_no_tools_available_it_still_floors() -> None:
    """Unchanged behaviour where there is nothing to escalate TO. Flooring is
    correct here — the alternative is showing the user raw ACTION: text."""
    from stackowl.owls.registry import OwlRegistry
    from stackowl.pipeline.services import StepServices, reset_services, set_services
    from stackowl.pipeline.steps import execute as exe

    services = StepServices(
        provider_registry=_FakeProviderRegistry(_LeakyStreamProvider()),  # type: ignore[arg-type]
        tool_registry=None,
        owl_registry=OwlRegistry.with_default_secretary(),
    )

    stoken = set_services(services)
    try:
        out = await exe.run(_state())
    finally:
        reset_services(stoken)

    assert any(se.exc_type == "ToolCallLeakError" for se in out.step_errors), (
        f"with no tools there is nothing to escalate to: {out.step_errors}"
    )
    assert out.responses == (), "nothing from the leaked stream may reach the user"
