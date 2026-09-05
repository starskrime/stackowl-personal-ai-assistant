"""Adding a tool is not a one-off cost — it is a tax on every round of every turn.

THE OPERATOR'S DECISION THIS RESTS ON (ESC-149, 2026-09-05): assume the gateway does
NOT cache. So the re-sent prefix is REAL spend — 384,429,704 tokens, 64% of the primary
provider's entire input bill, is prefix already sent earlier in the same turn.

WHAT THE PREFIX ACTUALLY COSTS, measured 2026-09-05 over 6,772 traces:

    percentile   prefix-carrying rounds   prefix cost   share of the 500,000 turn cap
    p50                    2                  39,802                  8.0%
    p90                    8                 159,208                 31.8%
    p99                   21                 417,921                 83.6%

**So the prefix is not a problem for 90% of turns and is catastrophic for the 1% —
which are exactly the turns that die.** That asymmetry is why this file guards a CEILING
instead of shrinking anything: a static cut sized to help the tail would gut the body.
Computed, at a 50% share of the turn cap: sizing the tool budget at p90 changes NOTHING
today, while sizing it at p99 admits only 4 of the 57 discretionary tools. There is no
single static number that helps the tail without taxing everyone.

WHAT THIS GUARD IS FOR, then. Nothing in the tree prices a tool by its RECURRING cost.
Admission asks "does it fit the 262,144-token window" — a question about one request —
while the tool is then paid for once per round against a 500,000-token TURN cap. 77 tools
cost ~19,901 tokens, and the registry's own comment records that the block cost ~7,524
when `tool_count_cap` was 30. It has grown 2.6x, the cap is now 150, and **zero eviction
events appear in any retained log**, so nothing would notice it growing again.

The bound is expressed at p90 deliberately: it is the round count most real work reaches,
so it protects the body of the distribution rather than the tail that a cut cannot fix.
"""

from __future__ import annotations

import json

import pytest

from stackowl.authz.bounds import DEFAULT_TURN_MAX_INPUT_TOKENS

#: Measured 2026-09-05 over 6,772 traces carrying at least one prefix-bearing round.
#: p90, not p99: this guards the body of the distribution. See the module docstring for
#: why the tail is deliberately out of scope.
P90_PREFIX_ROUNDS = 8

#: Share of a turn's whole token budget the tool block may consume, at p90 rounds,
#: before a human should look. Today's value is 31.8%; 40% leaves roughly 20 more tools
#: of headroom, which is exactly the growth increment that would move the token cap's
#: crossing point from ~20 rounds to ~17 and shorten every turn on the platform.
MAX_TOOL_SHARE_OF_TURN = 0.40

#: The measured chars-per-token for this deployment, back-derived from provider-reported
#: `prompt_tokens` against the real prompt text — NOT the folklore 4.0, which is ~4% low
#: here and would understate the block.
CHARS_PER_TOKEN = 3.85


def _tool_block_tokens() -> tuple[int, int]:
    """(tool_count, tokens) for the schema array as it is actually emitted."""
    from stackowl.tools.registry import ToolRegistry

    tools = list(getattr(ToolRegistry.with_defaults(), "_tools", {}).values())
    chars = sum(
        len(json.dumps({
            "name": t.name,
            "description": t.description or "",
            "parameters": t.parameters or {},
        }))
        for t in tools
    )
    return len(tools), int(chars / CHARS_PER_TOKEN)


def _turn_cost_share(tokens: int) -> float:
    """Share of ONE turn's token budget the block costs across p90 rounds.

    ONE SOURCE for the arithmetic. It lived inline in the tripwire and was
    re-derived by the test that meant to pin it, so a mutant deleting the
    `* P90_PREFIX_ROUNDS` left every assertion green — the test was duplicating the
    code under test instead of exercising it. Both callers now ask this.
    """
    return tokens * P90_PREFIX_ROUNDS / DEFAULT_TURN_MAX_INPUT_TOKENS


@pytest.mark.tripwire
def test_the_tool_block_stays_affordable_across_a_whole_turn() -> None:
    """A tool is paid for on EVERY round. This is the only place that says so."""
    count, tokens = _tool_block_tokens()
    share = _turn_cost_share(tokens)
    cost = int(share * DEFAULT_TURN_MAX_INPUT_TOKENS)

    assert share <= MAX_TOOL_SHARE_OF_TURN, (
        f"the tool block is {tokens:,} tokens across {count} tools; at the measured p90 "
        f"of {P90_PREFIX_ROUNDS} prefix-carrying rounds that is {cost:,} tokens, "
        f"{share:.1%} of the {DEFAULT_TURN_MAX_INPUT_TOKENS:,}-token turn cap "
        f"(ceiling {MAX_TOOL_SHARE_OF_TURN:.0%}).\n"
        "Admission only ever asked whether a tool fits the CONTEXT WINDOW, which is a "
        "question about one request; a tool is then re-sent once per round. Options, in "
        "the order the evidence supports them: demote rarely-used tools to the "
        "discretionary tier (they stay reachable — `tool_search` and `tool_describe` are "
        "guaranteed, and tool_search has 991 live invocations), shorten the largest "
        "descriptions, or delete what is never invoked."
    )


def test_the_guard_has_real_headroom_and_is_not_already_breached() -> None:
    """A tripwire that ships already-red teaches people to disable it; one that ships
    with no headroom fires on the next commit. Both are failures of calibration, so
    the actual margin is asserted rather than assumed."""
    _, tokens = _tool_block_tokens()
    ceiling = DEFAULT_TURN_MAX_INPUT_TOKENS * MAX_TOOL_SHARE_OF_TURN / P90_PREFIX_ROUNDS

    assert tokens < ceiling, "already breached on the day it shipped"
    assert tokens > ceiling * 0.5, (
        f"the tool block ({tokens:,}) is under half the ceiling ({ceiling:,.0f}) — the "
        "guard is so loose it would never fire, which is decoration"
    )


def test_the_discovery_path_survives_any_demotion() -> None:
    """The guard's advice is only safe while discovery is non-evictable. If
    `tool_search` or `tool_describe` ever leave the guaranteed tier, demoting a tool
    stops being a presentation change and becomes a capability removal — which is the
    `skills_list` incident, where a tool_count_cap of 30 starved it so it could never
    be called."""
    from stackowl.tools._infra.presentation import ToolPresentation
    from stackowl.tools.registry import ToolRegistry

    tools = list(getattr(ToolRegistry.with_defaults(), "_tools", {}).values())
    guaranteed, _ = ToolPresentation().rank_candidates(
        all_tools=tools, profile=None, pins=None, hydrated=None
    )
    names = {t.name for t in guaranteed}

    for needed in ("tool_search", "tool_describe"):
        assert needed in names, (
            f"{needed} is no longer guaranteed — a demoted tool would become "
            "undiscoverable, turning a presentation change into a capability cut"
        )


def test_the_cost_is_MULTIPLIED_by_rounds_not_counted_once() -> None:
    """THE WHOLE POINT, and nothing else pinned it.

    Dropping the `* P90_PREFIX_ROUNDS` would leave every assertion above green while
    the guard silently measured a one-off cost — reproducing the exact defect this
    file exists to name: admission prices a tool as if it were paid once, when it is
    paid on every round. Found by asking what a mutant would survive.
    """
    _, tokens = _tool_block_tokens()
    share = _turn_cost_share(tokens)

    assert share * DEFAULT_TURN_MAX_INPUT_TOKENS > tokens * 4, (
        "the recurring multiplication is missing — a tool priced once is the bug, "
        "not the guard"
    )
    assert share > 0.20, (
        f"at {tokens:,} tok x {P90_PREFIX_ROUNDS} rounds the block should already be a "
        "meaningful share of the turn; a share this small means the arithmetic changed"
    )
