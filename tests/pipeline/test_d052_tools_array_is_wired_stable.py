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
        restrict_to=None, usage_scores=None, global_usage_scores=None,
        budget=None, max_tools=None,
    ):
        # `global_usage_scores` + `max_tools` mirror the REAL
        # ToolRegistry.to_provider_schema. This double pinned an explicit
        # signature, so it went red the moment production grew a parameter —
        # which is the point of an explicit signature and why it is widened here
        # rather than loosened to **kwargs: a double that silently swallows new
        # arguments stops telling you the real thing moved.
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


async def _run_turn_with_registry(
    registry, sink, *, history_messages: int, window: int | None = None,
) -> None:
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
            model_window=window,
            history=tuple(
                Message(role="user", content="x" * 400)
                for _ in range(history_messages)
            ),
        )
        await execute.run(state)
    finally:
        reset_services(token)


async def _run_turn(registry, sink, *, history_messages: int) -> None:
    await _run_turn_with_registry(
        registry, sink, history_messages=history_messages,
    )


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
async def test_the_real_registry_and_real_budgeter_are_stable_across_turns():
    """Same property, with ONLY the AI provider mocked.

    The tests above substitute the registry, which is what makes the budget
    behaviour observable — but a fake registry can also hide an integration
    defect, and the standing rule for this codebase is that an implementation
    gets a test mocking only the provider. So this one runs the REAL
    ToolRegistry.with_defaults(), the REAL ToolPresentation, and the REAL
    fit_items against a window small enough that the budget genuinely binds.

    Without the memo, turn 2's larger history shrinks the real fitted set.
    """
    from stackowl.tools.registry import ToolRegistry

    real = ToolRegistry.with_defaults()
    sink: list = []

    # A small window so `window - fixed_cost` actually constrains the real
    # budgeter rather than admitting the whole catalogue on both turns.
    await _run_turn_with_registry(real, sink, history_messages=0, window=16_000)
    await _run_turn_with_registry(real, sink, history_messages=40, window=16_000)

    assert len(sink) == 2
    assert [s["name"] for s in sink[0]] == [s["name"] for s in sink[1]], (
        f"the real presented set moved: {len(sink[0])} tools -> {len(sink[1])}"
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


# ---------------------------------------------------------------------------
# D05.4 — losing the memo must not change the ANSWER
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_wiped_memo_rebuilds_the_SAME_array():
    """`infra/presented_tools.py` says losing the memo is "never a correctness
    issue". That is the claim under test, and nothing had ever checked it.

    It matters because the memo is wiped far more often than its own design
    assumed. `_on_capability_change` drops EVERY memoized array platform-wide on
    any capability flip, justified by "Capability flips are rare" — measured
    false: 950 all-time, 62 in a single day, 100% of them `browser`, from a
    ~3-second subprocess recycle.

    So this is the real question: when that wipe lands mid-conversation, does the
    rebuild reproduce what the model already had? If it does, the memo is a cache.
    If it does not, the memo is the only thing holding Law 1 and every browser
    bounce silently changes the agent's capabilities.

    Real registry, real budgeter, only the AI provider mocked.
    """
    from stackowl.tools.registry import ToolRegistry

    real = ToolRegistry.with_defaults()
    sink: list = []

    await _run_turn_with_registry(real, sink, history_messages=0, window=16_000)
    # Exactly what a browser recycle does, 62 times a day — the PRODUCTION
    # trigger, not `clear()`. The two are deliberately different since D05.4 and
    # calling the wrong one would make this test pass by exercising a path
    # nothing takes.
    presented_tools._on_capability_change("browser")
    await _run_turn_with_registry(real, sink, history_messages=40, window=16_000)

    assert len(sink) == 2
    before = [s["name"] for s in sink[0]]
    after = [s["name"] for s in sink[1]]
    assert before == after, (
        "a memo wipe mid-conversation changed the presented tools: "
        f"{len(before)} -> {len(after)}. "
        f"Lost: {sorted(set(before) - set(after))}. "
        f"Gained: {sorted(set(after) - set(before))}."
    )
