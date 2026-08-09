"""D01.1 — the system prompt has a STABLE tier and a VOLATILE tier.

WHY THIS EXISTS. D01.1's design says it adopted the reference platform' split and then "froze
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

STAGE 1 was a pure refactor: the tiers existed as named functions while
``build_base_prompt`` still composed the old text. Both callers have since moved
— ``assemble`` builds from :func:`build_stable_base_prompt`, and ``execute``
delivers :func:`volatile_turn_context` with the turn — so the CLEANUP stage
removed ``operational_adapter`` / ``build_base_prompt`` as duplicated text.

That removal took with it the equivalence test that used to prove the split
changed nothing the model sees, because there is no longer an old path to be
equivalent TO. The GOLDEN SNAPSHOTS below replace that proof: they pin each
tier's output byte-for-byte, so the guarantee survives the disappearance of its
former comparand. They were written and made to pass BEFORE the deletion, so
they pin the text as it actually shipped rather than whatever survived.
"""

from __future__ import annotations

import datetime

from stackowl.owls.base_prompt import (
    build_stable_base_prompt,
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


# ---------------------------------------------------------------------------
# GOLDEN SNAPSHOTS — the regression proof that replaced the equivalence test.
#
# These are deliberately written out in full rather than built from the module's
# own constants. A snapshot assembled from `_CALL_PROTOCOL` and friends would
# pass no matter how those constants were edited, which is precisely the drift
# it exists to catch. The duplication is the point.
# ---------------------------------------------------------------------------

_GOLDEN_STABLE = (
    "Operational context (this changes; your character above does not).\n\n"
    "To use a capability, output exactly:\n"
    "ACTION: <name>\n"
    "```json\n"
    '{"<arg>": "<value>"}\n'
    "```\n"
    "Then stop and wait for the OBSERVATION (the result) before continuing. "
    "The capabilities currently available to you are listed separately; use "
    "their exact names in place of <name>.\n\n"
    "When you fetch or save a file for the user, write it into the workspace's "
    "downloads/ folder, so it can be delivered to them and is cleaned up "
    "automatically over time."
)

_GOLDEN_STABLE_NO_PROTOCOL = (
    "Operational context (this changes; your character above does not).\n\n"
    "When you fetch or save a file for the user, write it into the workspace's "
    "downloads/ folder, so it can be delivered to them and is cleaned up "
    "automatically over time."
)

_GOLDEN_NO_CAPABILITIES = (
    "No capabilities are available to you this turn. Do not attempt to "
    "call a function, tool, or capability of any kind, in any format — "
    "answer entirely from your own knowledge instead."
)


def test_golden_stable_tier_text() -> None:
    """Byte-exact. This is the cached prefix: a single character changed here
    invalidates every stored SessionPrompt on the next rollover, so the change
    should be deliberate enough to update a golden file for."""
    assert stable_operational_context() == _GOLDEN_STABLE


def test_golden_stable_tier_text_without_protocol() -> None:
    assert stable_operational_context(describe_tool_protocol=False) == (
        _GOLDEN_STABLE_NO_PROTOCOL
    )


def test_golden_volatile_tier_text() -> None:
    """The clock line is the whole volatile tier when capabilities ARE offered."""
    human_now = NOW.strftime("%A, %B %d, %Y at %I:%M %p %Z").strip()
    assert volatile_turn_context(NOW) == f"Right now it is {human_now}."


def test_golden_volatile_tier_text_when_no_capabilities() -> None:
    """The negative instruction is VOLATILE, not stable: "this turn" is a claim
    about one turn, so it cannot live in a frozen prompt. It is an explicit
    prohibition rather than silence, because silence does not stop a natively
    tool-trained model attempting its own calling convention."""
    human_now = NOW.strftime("%A, %B %d, %Y at %I:%M %p %Z").strip()
    assert volatile_turn_context(NOW, capabilities_offered=False) == (
        f"Right now it is {human_now}.\n\n{_GOLDEN_NO_CAPABILITIES}"
    )


def test_the_frozen_prompt_is_charter_then_stable_tier() -> None:
    """What `assemble` actually freezes. The charter leads (strongest, durable
    signal first) and NO clock appears anywhere in it — the property that makes
    a byte-identical prompt possible at all."""
    from stackowl.owls.base_prompt import behavioral_charter

    composed = build_stable_base_prompt()

    assert composed == behavioral_charter() + "\n\n" + _GOLDEN_STABLE
    assert "Right now it is" not in composed
    assert composed.index("ACTION:") > 0
