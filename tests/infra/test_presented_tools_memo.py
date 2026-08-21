"""D05.2 — the presented-tools memo, which fixes Defect B.

Defect A (ordering by the turn's request text) is pinned in
tests/tools/test_presentation_budget.py. This file pins the SECOND, independent
cause of the same instability, the one no earlier analysis named:

    execute.py     _fixed_cost = est(system_prompt) + est(EVERY history message)
    context_budget tool_budget_tokens = window - fixed_cost

History grows every turn, so the tool budget shrinks every turn and fit_items
admits fewer candidates — under a perfectly stable ordering. That is what made
D01.3's measured tool COUNT oscillate (5→4→5→5→5 on one lane); an ordering defect
reshuffles a fixed-size array but cannot change its length.

MUTATION CHECK (run by hand, recorded here because it is the point of the file):
revert the memo in execute.build_tool_schemas while KEEPING the learned ordering.
test_history_growth_does_not_shrink_the_array must fail and the ordering tests in
test_presentation_budget.py must still pass. If both survive, one fix is being
credited for two defects.
"""

from __future__ import annotations

import pytest

from stackowl.infra import presented_tools
from stackowl.tools.registry import ToolRegistry
from tests.tools.test_presentation_budget import _RT


@pytest.fixture(autouse=True)
def _clean_memo():
    presented_tools.clear()
    yield
    presented_tools.clear()


def _registry() -> ToolRegistry:
    reg = ToolRegistry()
    for n in ("read_file", "write_file", "tool_search", "tool_describe"):
        reg.register(_RT(n))
    for n in ("send_email", "web_search", "calendar_create", "note_tool",
              "task_create", "task_list", "task_update", "browser_open",
              "image_gen", "voice_record"):
        reg.register(_RT(n, group="misc"))
    return reg


def _build(reg: ToolRegistry, *, fixed_cost: int) -> list[dict]:
    """The unmemoized build, i.e. what execute.py does on a memo MISS."""
    return reg.to_provider_schema(
        "openai", budget={"window": 8192, "fixed_cost_tokens": fixed_cost},
    )


# --------------------------------------------------------------------------- #
# The defect itself — asserted directly, so the fix cannot be mistaken for a
# no-op on a codebase where the budget never actually bound.
# --------------------------------------------------------------------------- #


def test_the_defect_is_real_growing_history_shrinks_the_unmemoized_array():
    """WITHOUT the memo, a longer history yields a smaller array.

    If this ever stops holding, the memo below is protecting nothing and this
    whole file is theatre — so it is asserted rather than assumed.
    """
    reg = _registry()
    early = _build(reg, fixed_cost=100)     # turn 1: short history
    late = _build(reg, fixed_cost=8000)     # turn N: history has grown
    assert len(late) < len(early)


def test_history_growth_does_not_shrink_the_array_through_the_memo():
    """THE FIX. One session, one key → the same array regardless of history."""
    reg = _registry()
    key = presented_tools.make_key(
        session_key="lane-1", owl="scout", provider="backend-a", protocol="openai",
        window=8192, hydrated=None,
    )
    first = _build(reg, fixed_cost=100)
    presented_tools.put(key, first)

    # Turn N: history has grown enormously. The memo answers before the budget
    # is ever consulted, which is exactly why the array cannot shrink.
    cached = presented_tools.get(key)
    assert cached == first
    assert len(cached) > len(_build(reg, fixed_cost=8000))


# --------------------------------------------------------------------------- #
# The key. Each of these is a named failure mode in D05.2's design doc; each
# would be silent in production.
# --------------------------------------------------------------------------- #


def test_a_different_session_does_not_hit():
    """Stability horizon is the SESSION. A new session must recompute, or a
    learned demotion could never take effect."""
    a = presented_tools.make_key(
        session_key="lane-1", owl="scout", provider="backend-a", protocol="openai", window=8192, hydrated=None)
    b = presented_tools.make_key(
        session_key="lane-2", owl="scout", provider="backend-a", protocol="openai", window=8192, hydrated=None)
    presented_tools.put(a, [{"name": "x"}])
    assert presented_tools.get(b) is None


def test_escalation_tiers_do_not_share_an_array():
    """build_tool_schemas is re-invoked per tier, and the tiers can speak
    different wire protocols and have different windows. A session-only key
    would hand an Anthropic-shaped array to an OpenAI-protocol tier."""
    fast = presented_tools.make_key(
        session_key="s", owl="scout", provider="backend-a", protocol="openai", window=8192, hydrated=None)
    powerful = presented_tools.make_key(
        session_key="s", owl="scout", provider="backend-a", protocol="anthropic", window=200_000, hydrated=None)
    presented_tools.put(fast, [{"function": {"name": "openai_shaped"}}])
    assert presented_tools.get(powerful) is None


def test_two_owls_on_one_lane_do_not_share_an_array():
    a = presented_tools.make_key(
        session_key="s", owl="scout", provider="backend-a", protocol="openai", window=8192, hydrated=None)
    b = presented_tools.make_key(
        session_key="s", owl="secretary", provider="backend-a", protocol="openai", window=8192, hydrated=None)
    presented_tools.put(a, [{"name": "x"}])
    assert presented_tools.get(b) is None


def test_a_tool_search_hydration_invalidates_so_promotion_still_works():
    """I2 BEATS I1 WHERE THEY COLLIDE.

    FX-07 promotes a tool_search hit into the NEXT turn's presented schema. Under
    a memo that ignored the hydrated set that promotion would silently stop
    working — the model searches, finds the tool, and never receives it. So a
    discovery invalidates and costs one rebuild.
    """
    before = presented_tools.make_key(
        session_key="s", owl="scout", provider="backend-a", protocol="openai", window=8192, hydrated=None)
    presented_tools.put(before, [{"name": "stale"}])
    after = presented_tools.make_key(
        session_key="s", owl="scout", provider="backend-a", protocol="openai", window=8192,
        hydrated={"image_gen"},
    )
    assert presented_tools.get(after) is None, (
        "a hydration must invalidate the memo, or tool_search promotion is dead"
    )


def test_hydrated_set_order_does_not_change_the_key():
    """Sets have no stable iteration order. If the key were built from one
    directly, two identical hydrated sets could hash differently and the memo
    would simply never hit — a silent, total loss of the fix."""
    k1 = presented_tools.make_key(
        session_key="s", owl="o", provider="backend-a", protocol="openai", window=1,
        hydrated={"a", "b", "c"})
    k2 = presented_tools.make_key(
        session_key="s", owl="o", provider="backend-a", protocol="openai", window=1,
        hydrated={"c", "a", "b"})
    assert k1 == k2


def test_get_returns_a_copy_so_a_caller_cannot_poison_the_memo():
    """execute.py filters the returned list in place for the depth>0 spawn-tool
    exclusion. Handing out the stored list would let one delegated child's
    exclusion persist onto every later turn of the parent's session."""
    key = presented_tools.make_key(
        session_key="s", owl="o", provider="backend-a", protocol="openai", window=1, hydrated=None)
    presented_tools.put(key, [{"name": "a"}, {"name": "spawn"}])
    got = presented_tools.get(key)
    got.pop()
    assert len(presented_tools.get(key)) == 2


def test_clear_owl_drops_that_owl_across_every_session():
    """An owl edit must reach the memo in EVERY session the owl is live in, not
    just the one that made the edit — otherwise a self-extending owl keeps being
    handed its pre-edit toolset on every other lane."""
    edited_a = presented_tools.make_key(
        session_key="s1", owl="scout", provider="backend-a", protocol="openai", window=1, hydrated=None)
    edited_b = presented_tools.make_key(
        session_key="s2", owl="scout", provider="backend-a", protocol="openai", window=1, hydrated=None)
    bystander = presented_tools.make_key(
        session_key="s1", owl="secretary", provider="backend-a", protocol="openai", window=1, hydrated=None)
    for k in (edited_a, edited_b, bystander):
        presented_tools.put(k, [{"name": "x"}])

    presented_tools.clear_owl("scout")

    assert presented_tools.get(edited_a) is None
    assert presented_tools.get(edited_b) is None
    assert presented_tools.get(bystander) is not None, (
        "an edit to one owl must not evict another owl's array"
    )


def test_clear_is_session_scoped():
    a = presented_tools.make_key(
        session_key="s1", owl="o", provider="backend-a", protocol="openai", window=1, hydrated=None)
    b = presented_tools.make_key(
        session_key="s2", owl="o", provider="backend-a", protocol="openai", window=1, hydrated=None)
    presented_tools.put(a, [{"name": "a"}])
    presented_tools.put(b, [{"name": "b"}])
    presented_tools.clear("s1")
    assert presented_tools.get(a) is None
    assert presented_tools.get(b) is not None
