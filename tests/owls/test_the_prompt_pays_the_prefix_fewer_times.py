"""The stable prompt tells the model to spend ROUNDS carefully — never capability.

MEASURED, trace f33c9fa0. An 18-character question ("What agents i have") billed
683,728 input tokens across 16 rounds. Where it went::

    79 tool schemas re-sent every round   310,768   45%
    system prompt re-sent every round      56,896    8%
    accumulating transcript               ~291,000   43%
    the model's own output, all 16 rounds   25,326  3.7%

THE CORRECTION THIS FILE EXISTS TO PIN. The first attempt at this was to present
FEWER TOOLS. That is wrong, and it is the same defect one layer down from the skill
catalogue we capped at 4,000 chars — the cap that produced a platform which could not
answer "what agents i have" in the first place. Tool schemas are how the model knows
what the platform CAN DO. Rationing them buys tokens with capability.

The arithmetic says the lever is elsewhere: 19,423 tokens is the price of ONE round.
310,768 is the price of SIXTEEN. The schemas are byte-identical every round; they are
not growing. So the fix is to need fewer round-trips, which costs nothing:

  * independent calls belong in ONE round — the reference platform instructs exactly
    this, for exactly this reason: batching avoids resending the whole conversation
    on every extra round-trip;
  * a round not taken saves the ENTIRE 22,978-token prefix, not just its own output.

WHY THE STABLE TIER. Law 1 — per-conversation prompt caching is sacred. This text is
timeless and identical every turn, so it rides the cached prefix and is paid for once
per session. Putting it in the volatile tier would cost more than it saves.
"""

from __future__ import annotations

from stackowl.owls.base_prompt import (
    stable_operational_context,
    volatile_turn_context,
)


def test_the_model_is_told_to_batch_independent_calls() -> None:
    """The one instruction that reduces rounds without reducing what it can do."""
    text = stable_operational_context().lower()
    assert "same round" in text or "one round" in text or "together" in text, (
        f"no batching guidance in the stable prompt:\n{text}"
    )
    assert "independent" in text or "do not depend" in text, (
        "batching must be scoped to INDEPENDENT calls — batching a call that needs "
        "the previous result produces a wrong call, not a cheaper turn"
    )


def test_the_model_is_told_to_STOP_when_it_can_answer() -> None:
    """Every round not taken saves the whole fixed prefix, not just its own output."""
    text = stable_operational_context().lower()
    assert "the moment you can answer" in text, (
        f"no early-stop guidance in the stable prompt:\n{text}"
    )
    assert "briefly" in text or "brief" in text, (
        "Bakir, 2026-08-29: do not generate long answers burning tokens, be "
        "concrete and short. That belongs in the tier paid for once per session."
    )


def test_it_names_NO_tool_and_NO_clock() -> None:
    """The file's own contract for this tier: timeless, infrastructure-agnostic.

    A tool name here would rot; a clock would break the byte-identical prefix that
    makes the tier worth having.
    """
    text = stable_operational_context()
    for forbidden in ("skills_list", "owl_list", "web_fetch", "browser_navigate", "shell"):
        assert forbidden not in text, f"the stable tier names a specific tool: {forbidden}"
    assert "202" not in text, "a date leaked into the frozen tier"


def test_it_is_BYTE_IDENTICAL_across_calls() -> None:
    """Law 1. If this tier varies, the whole cached prefix is re-billed every turn."""
    assert stable_operational_context() == stable_operational_context()
    assert (
        stable_operational_context(describe_tool_protocol=False)
        == stable_operational_context(describe_tool_protocol=False)
    )


def test_the_guidance_is_ABSENT_when_no_capabilities_are_offered() -> None:
    """Telling a turn with no tools how to batch tool calls is noise, and worse,
    it contradicts the explicit "do not attempt to call anything" instruction."""
    text = stable_operational_context(describe_tool_protocol=False).lower()
    assert "same round" not in text and "one round" not in text


def test_the_volatile_tier_did_not_grow() -> None:
    """The per-turn tier is paid for EVERY turn — this guidance must not land there."""
    import datetime

    now = datetime.datetime(2026, 8, 29, 20, 0, tzinfo=datetime.UTC)
    text = volatile_turn_context(now).lower()
    assert "round" not in text, (
        f"round-economy guidance leaked into the per-turn tier:\n{text}"
    )
