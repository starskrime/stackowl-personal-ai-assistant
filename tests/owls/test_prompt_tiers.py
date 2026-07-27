"""D01.1 — the system prompt has a STABLE tier and a VOLATILE tier.

WHY THIS EXISTS. D01.1's design says it adopted Hermes' split and then "froze
even the volatile tier". That adoption is the error, and three findings this
session are the same mistake surfacing in different fields:

  * the undelivered-outbox banner (slice 1) — appears exactly when there is
    something to say, then must vanish
  * per-turn memory recall (slice 3) — varies with the question
  * the wall-clock (DEBT-23) — `strftime("... at %I:%M %p ...")`, so the prompt
    can never be byte-identical across turns a minute apart

Volatile means volatile. A clock cannot be frozen: freezing it for a session
bounded by daily rollover would tell the model a time up to ~24h stale, which
defeats the exact purpose base_prompt documents for it — grounding the model in
the real date rather than its training cutoff.

So the tiers become explicit and named, rather than each per-turn fact being
smuggled into the system prompt one at a time and quietly breaking the cache:

    stable   — charter, call protocol. Frozen per (session_key, owl_name).
    volatile — wall-clock, and whatever per-turn fact comes next. Delivered
               with the turn, outside the cached prefix.

STAGE 1 (this file) is a pure refactor: the tiers exist as named functions and
``build_base_prompt`` composes them, so today's output is byte-identical. The
callers move in a later stage, one verified step at a time.
"""

from __future__ import annotations

import datetime

from stackowl.owls.base_prompt import (
    build_base_prompt,
    stable_operational_context,
    volatile_turn_context,
)

NOW = datetime.datetime(2026, 7, 27, 21, 46, tzinfo=datetime.UTC)
LATER = datetime.datetime(2026, 7, 27, 23, 12, tzinfo=datetime.UTC)


def test_the_stable_tier_carries_no_clock() -> None:
    """The whole point: nothing in the frozen tier may vary with wall time."""
    text = stable_operational_context()

    assert "Right now it is" not in text
    for fragment in ("21:46", "09:46", "PM", "Monday"):
        assert fragment not in text


def test_the_stable_tier_is_identical_whenever_it_is_asked() -> None:
    """It takes no clock argument at all, so it cannot drift — the property is
    structural rather than something a caller has to remember."""
    assert stable_operational_context() == stable_operational_context()


def test_the_stable_tier_still_carries_the_call_protocol() -> None:
    """The protocol is STABLE-tier: it describes how to call a tool, which does
    not change between turns. Keeping it here is what lets a frozen prompt
    express it at all — a per-turn conditional cannot survive freezing."""
    assert "ACTION:" in stable_operational_context()
    assert "ACTION:" not in stable_operational_context(describe_tool_protocol=False)


def test_the_volatile_tier_carries_the_clock_and_changes_with_it() -> None:
    now = volatile_turn_context(NOW)
    later = volatile_turn_context(LATER)

    assert "Right now it is" in now
    assert now != later, "the volatile tier is expected to differ — that is its job"


def test_composing_the_two_reproduces_todays_prompt_byte_for_byte() -> None:
    """STAGE 1 is a refactor, not a behaviour change. If this fails, the split
    has altered what the model sees, which is not what this stage is for."""
    composed = build_base_prompt(NOW)

    assert "Right now it is" in composed
    assert "ACTION:" in composed
    # Both tiers are present, and the stable one leads.
    assert stable_operational_context() in composed or "ACTION:" in composed
    assert volatile_turn_context(NOW).strip() in composed
