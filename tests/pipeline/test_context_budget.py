from stackowl.pipeline.context_budget import (
    HARD_TOOL_COUNT_CAP,
    PROMPT_SAFETY_FRACTION,
    RESPONSE_RESERVE_TOKENS,
    fit_items,
    tool_budget_tokens,
)


def test_tool_budget_subtracts_reserve_and_fixed_cost():
    b = tool_budget_tokens(window=8192, fixed_cost_tokens=1000)
    assert b == int(8192 * PROMPT_SAFETY_FRACTION) - RESPONSE_RESERVE_TOKENS - 1000


def test_fit_keeps_all_guaranteed_even_when_over_budget():
    out = fit_items(
        guaranteed=["g1", "g2"], candidates=["c1", "c2"],
        budget=300, size_of=lambda _x: 500, hard_cap=HARD_TOOL_COUNT_CAP,
    )
    assert out == ["g1", "g2"]


def test_fit_adds_candidates_in_order_until_budget_spent():
    out = fit_items(
        guaranteed=[], candidates=["c1", "c2", "c3"],
        budget=250, size_of=lambda _x: 100, hard_cap=HARD_TOOL_COUNT_CAP,
    )
    assert out == ["c1", "c2"]


def test_hard_cap_backstops_count():
    out = fit_items(
        guaranteed=[], candidates=[f"c{i}" for i in range(100)],
        budget=10_000_000, size_of=lambda _x: 1, hard_cap=5,
    )
    assert len(out) == 5


def test_guaranteed_consume_budget_before_candidates():
    out = fit_items(
        guaranteed=["g"], candidates=["c"],
        budget=250, size_of=lambda x: 200 if x == "g" else 100, hard_cap=HARD_TOOL_COUNT_CAP,
    )
    assert out == ["g"]
