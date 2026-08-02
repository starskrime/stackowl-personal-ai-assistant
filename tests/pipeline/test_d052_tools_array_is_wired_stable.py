"""D05.2 — proof the memo is WIRED, not merely correct.

tests/infra/test_presented_tools_memo.py proves the memo module behaves. It does
NOT prove execute.py consults it, and that distinction is not academic:

    MUTATION M1 — replace execute.build_tool_schemas' memo lookup with
    `cached = None`, leaving the module and the learned ordering untouched.

M1 SURVIVED the module tests. All 19 passed with the memo disabled in the
pipeline, because none of them go through execute.py. Under M1 the shipped
platform would have rebuilt the array every turn — the exact defect the item
claims to fix — while the suite stayed green.

The test below is the one M1 kills. It drives execute.run twice on ONE session
with a history that grows in between, and asserts the provider received a
byte-identical tools array both times.

The fake registry deliberately models the real budgeter's failure mode: it
returns fewer schemas as `fixed_cost_tokens` rises, which is precisely what
context_budget.fit_items does when history growth eats the window. Without the
memo the second turn gets a shorter array and the assertion fires.
"""

from __future__ import annotations

import pytest

from stackowl.infra import presented_tools


@pytest.fixture(autouse=True)
def _clean_memo():
    presented_tools.clear()
    yield
    presented_tools.clear()


class _BudgetSensitiveRegistry:
    """Returns fewer tools as the fixed cost rises — i.e. as history grows.

    This is not a strawman: fit_items admits candidates only while they fit in
    `window - fixed_cost_tokens`, so a growing history genuinely shrinks the
    presented set. Modelling it is what lets this test observe Defect B without
    needing a real 40-turn conversation.
    """

    def __init__(self) -> None:
        self.calls: list[int] = []

    def all(self):
        return [object()]  # truthy → the tool-loop branch is taken

    def to_provider_schema(
        self, protocol, *, profile=None, pins=None, hydrated=None,
        restrict_to=None, usage_scores=None, budget=None,
    ):
        fixed = (budget or {}).get("fixed_cost_tokens", 0)
        self.calls.append(fixed)
        # 10 tools with an empty history, dropping one per 100 tokens of it.
        count = max(1, 10 - fixed // 100)
        return [{"name": f"tool_{i}"} for i in range(count)]

    def get(self, name):
        return None


class _FakeProvider:
    protocol = "anthropic"

    def __init__(self, sink: list) -> None:
        self._sink = sink

    async def complete_with_tools(
        self, user_text, system_text, tool_schemas, tool_dispatcher,
        max_iterations=8, history=None, **_kwargs,
    ):
        self._sink.append(list(tool_schemas))
        return "ok", []


class _FakeProviderRegistry:
    def __init__(self, provider) -> None:
        self._p = provider

    def get(self, name):
        return self._p

    def get_by_tier(self, tier):
        return self._p


async def _run_turn(registry, sink, *, history_messages: int) -> None:
    from stackowl.pipeline.services import StepServices, reset_services, set_services
    from stackowl.pipeline.state import PipelineState
    from stackowl.pipeline.steps import execute
    from stackowl.providers.base import Message

    services = StepServices(
        provider_registry=_FakeProviderRegistry(_FakeProvider(sink)),
        tool_registry=registry,
    )
    token = set_services(services)
    try:
        state = PipelineState(
            trace_id=f"t-{history_messages}", session_key="one-session",
            input_text="a question", channel="cli", owl_name="secretary",
            pipeline_step="execute", system_prompt="SYS",
            history=tuple(
                Message(role="user", content="x" * 400)
                for _ in range(history_messages)
            ),
        )
        await execute.run(state)
    finally:
        reset_services(token)


@pytest.mark.asyncio
async def test_the_defect_reaches_the_provider_without_the_memo():
    """Sanity: the fake registry really does shrink under a growing history.

    Asserted on two SEPARATE sessions so the memo cannot mask it. If this ever
    stops holding, the test below is passing for the wrong reason.
    """
    reg = _BudgetSensitiveRegistry()
    short = reg.to_provider_schema("anthropic", budget={"fixed_cost_tokens": 0})
    long_ = reg.to_provider_schema("anthropic", budget={"fixed_cost_tokens": 900})
    assert len(long_) < len(short)


@pytest.mark.asyncio
async def test_a_growing_history_does_not_change_the_tools_the_provider_receives():
    """THE WIRING TEST. Killed by mutation M1 (`cached = None` in execute.py).

    Two turns, one session, a much larger history on the second. The provider
    must receive the same array both times.
    """
    reg = _BudgetSensitiveRegistry()
    sink: list = []

    await _run_turn(reg, sink, history_messages=0)
    await _run_turn(reg, sink, history_messages=12)

    assert len(sink) == 2, f"expected two provider calls, got {len(sink)}"
    assert sink[0] == sink[1], (
        "the tools array changed between turns of one session — the memo is not "
        f"wired. Turn 1 had {len(sink[0])} tools, turn 2 had {len(sink[1])}. "
        f"Registry saw fixed_cost values {reg.calls}."
    )


@pytest.mark.asyncio
async def test_the_second_turn_does_not_rebuild_at_all():
    """Stronger than equality: the budgeter must not even be CONSULTED again.

    Equality alone could hold by luck if a rebuild happened to fit the same
    count. Asserting the registry saw exactly one call proves the memo answered
    before the budget was ever computed — which is the actual mechanism.
    """
    reg = _BudgetSensitiveRegistry()
    sink: list = []

    await _run_turn(reg, sink, history_messages=0)
    calls_after_first = len(reg.calls)
    await _run_turn(reg, sink, history_messages=12)

    assert calls_after_first == 1
    assert len(reg.calls) == 1, (
        f"the second turn rebuilt the array (registry calls: {reg.calls})"
    )
