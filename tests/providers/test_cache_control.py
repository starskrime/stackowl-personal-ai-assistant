"""D01.2 — cache-breakpoint placement, the pure half.

Every assertion here is about the SHAPE OF THE OUTGOING REQUEST, because that is
the only half of this item that can be proven on this box. No Anthropic backend
is configured here, so whether Anthropic actually honours these markers is
explicitly NOT tested — see designs/D01.2.md, "WHAT CANNOT BE VERIFIED HERE".

The canned token counts and marker shapes below are built from the Anthropic API
documentation, NOT captured from the real API. A passing test here proves we send
what the documentation describes; it does not prove the documentation matches the
live service.
"""

from __future__ import annotations

import copy
from typing import Any

from stackowl.providers._cache_control import (
    MIN_CACHEABLE_TOKENS,
    apply_cache_breakpoints,
)

# ---------------------------------------------------------------------------
# Fixtures — sized so the cumulative prefix clears MIN_CACHEABLE_TOKENS.
#
# A breakpoint caches everything from the START of the request up to the marker,
# so what has to clear the minimum is the CUMULATIVE prefix, never the individual
# span. These fixtures make the tools array alone clear it, so a test that wants
# the short-tools case has to ask for it explicitly.
# ---------------------------------------------------------------------------

def _big_text(tokens: int) -> str:
    """Text whose conservative char estimate is at least ``tokens`` tokens."""
    # _estimate_tokens deliberately UNDER-counts (chars / 4 * 0.8), so produce
    # enough characters to clear the bar even after that discount.
    return "x" * (tokens * 5 + 8)


def _tools(tokens: int = 600) -> list[dict[str, Any]]:
    return [
        {"name": "shell", "description": "run a command", "input_schema": {"type": "object"}},
        {"name": "memory", "description": _big_text(tokens), "input_schema": {"type": "object"}},
    ]


def _messages() -> list[dict[str, Any]]:
    return [
        {"role": "user", "content": "first question"},
        {"role": "assistant", "content": [{"type": "text", "text": "first answer"}]},
        {"role": "user", "content": "second question"},
    ]


def _markers(obj: Any) -> list[dict[str, Any]]:
    """Every ``cache_control`` value anywhere inside ``obj``, in document order."""
    found: list[dict[str, Any]] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key == "cache_control":
                found.append(value)
            else:
                found.extend(_markers(value))
    elif isinstance(obj, list):
        for item in obj:
            found.extend(_markers(item))
    return found


def _all_markers(tools: Any, system: Any, messages: Any) -> list[dict[str, Any]]:
    return _markers(tools) + _markers(system) + _markers(messages)


# ---------------------------------------------------------------------------
# The layout: tools + full system + last two messages == exactly four
# ---------------------------------------------------------------------------

def test_full_request_gets_the_four_designed_breakpoints() -> None:
    tools, system, messages = apply_cache_breakpoints(
        _tools(), _big_text(800), _messages(), model="claude-opus-5", ttl="5m",
    )

    # marker 1 — the END of the tools array (position 0, the largest shared span)
    assert tools[-1]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in tools[0]

    # marker 2 — the end of the system prompt, which is now a block list
    assert isinstance(system, list)
    assert system[-1]["cache_control"] == {"type": "ephemeral"}

    # markers 3 and 4 — the last two messages
    assert _markers(messages[-1]) == [{"type": "ephemeral"}]
    assert _markers(messages[-2]) == [{"type": "ephemeral"}]
    assert _markers(messages[0]) == []

    assert len(_all_markers(tools, system, messages)) == 4


def test_never_places_more_than_four_markers() -> None:
    """I1 — the hard API limit, and the reason ONE module owns all marking."""
    long_conversation = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"turn {i}"}
        for i in range(40)
    ]
    tools, system, messages = apply_cache_breakpoints(
        _tools(), _big_text(800), long_conversation, model="claude-opus-5", ttl="5m",
    )
    assert len(_all_markers(tools, system, messages)) <= 4


def test_marks_the_last_two_messages_only() -> None:
    tools, system, messages = apply_cache_breakpoints(
        _tools(), _big_text(800), _messages(), model="claude-opus-5", ttl="5m",
    )
    marked = [i for i, m in enumerate(messages) if _markers(m)]
    assert marked == [len(messages) - 2, len(messages) - 1]


def test_a_single_message_gets_one_marker_not_two() -> None:
    tools, system, messages = apply_cache_breakpoints(
        _tools(), _big_text(800), [{"role": "user", "content": "only"}],
        model="claude-opus-5", ttl="5m",
    )
    assert len(_markers(messages)) == 1


# ---------------------------------------------------------------------------
# I2 — the minimum guard, applied to the CUMULATIVE prefix
# ---------------------------------------------------------------------------

def test_no_marker_at_all_when_the_whole_request_is_below_the_minimum() -> None:
    """I2 — a below-minimum marker is silently dead, so it is never placed."""
    tools, system, messages = apply_cache_breakpoints(
        [{"name": "t", "description": "d", "input_schema": {}}],
        "short system",
        [{"role": "user", "content": "hi"}],
        model="claude-opus-5",
        ttl="5m",
    )
    assert _all_markers(tools, system, messages) == []


def test_short_tools_lose_their_marker_but_a_long_system_still_gets_one() -> None:
    """The guard is CUMULATIVE: tools alone may miss while tools+system clears.

    This is the case that makes a per-span guard wrong. A breakpoint caches the
    whole prefix before it, so marker 2's eligibility is not marker 1's.
    """
    tools, system, messages = apply_cache_breakpoints(
        [{"name": "t", "description": "d", "input_schema": {}}],  # tiny
        _big_text(900),                                            # large
        _messages(),
        model="claude-opus-5",
        ttl="5m",
    )
    assert _markers(tools) == []
    assert _markers(system) == [{"type": "ephemeral"}]
    assert len(_markers(messages)) == 2


def test_two_spans_that_each_miss_the_floor_still_clear_it_together() -> None:
    """The guard is CUMULATIVE — and this is the only test that proves it.

    ``test_short_tools_lose_their_marker...`` above cannot: there the system span
    clears the floor on its own, so a per-span guard and a cumulative one give the
    same answer and the test passes either way. Mutation-testing caught that.

    Here NEITHER span clears 512 alone (~330 and ~301 tokens) but together they
    do, so a per-span guard drops the system marker and a cumulative one keeps it.
    """
    tools, system, messages = apply_cache_breakpoints(
        _tools(300), _big_text(300), _messages(), model="claude-opus-5", ttl="5m",
    )
    # tools alone is below the floor, so marker 1 is correctly skipped ...
    assert _markers(tools) == []
    # ... but tools+system is above it, so marker 2 IS placed.
    assert _markers(system) == [{"type": "ephemeral"}]


def test_measured_tokens_override_the_char_estimate() -> None:
    """The live count_tokens measurement wins over the fallback heuristic.

    Text long enough to pass the char estimate is REJECTED when the provider's
    real measurement says it is below the minimum — proving the estimate is a
    fallback, not the authority.
    """
    tools, system, messages = apply_cache_breakpoints(
        _tools(), _big_text(800), _messages(),
        model="claude-opus-5",
        ttl="5m",
        measured_tokens={"tools": 10, "system": 12},
    )
    assert _markers(tools) == []
    assert _markers(system) == []


def test_an_explicit_higher_minimum_suppresses_markers() -> None:
    """I2 with a KNOWN model floor — 4096-floor models cache none of this."""
    tools, system, messages = apply_cache_breakpoints(
        _tools(), _big_text(800), _messages(),
        model="claude-haiku-4-5-20251001",
        ttl="5m",
        minimum_tokens=4096,
    )
    assert _all_markers(tools, system, messages) == []


# ---------------------------------------------------------------------------
# Wire shape — convert ONLY when actually marking (Bakir's third contradiction)
# ---------------------------------------------------------------------------

def test_system_string_is_left_a_string_when_nothing_is_marked() -> None:
    """Blast radius matches benefit: an unmarked request stays byte-identical."""
    _, system, _ = apply_cache_breakpoints(
        None, "short system", [{"role": "user", "content": "hi"}],
        model="claude-opus-5", ttl="5m",
    )
    assert system == "short system"


def test_system_string_becomes_a_block_list_only_when_marked() -> None:
    text = _big_text(900)
    _, system, _ = apply_cache_breakpoints(
        _tools(), text, _messages(), model="claude-opus-5", ttl="5m",
    )
    assert system == [
        {"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}
    ]


def test_an_existing_system_block_list_is_marked_on_its_last_block() -> None:
    _, system, _ = apply_cache_breakpoints(
        _tools(),
        [{"type": "text", "text": _big_text(900)}, {"type": "text", "text": "tail"}],
        _messages(),
        model="claude-opus-5",
        ttl="5m",
    )
    assert "cache_control" not in system[0]
    assert system[1]["cache_control"] == {"type": "ephemeral"}


def test_a_string_message_becomes_a_block_list_when_marked() -> None:
    _, _, messages = apply_cache_breakpoints(
        _tools(), _big_text(800), [{"role": "user", "content": "only"}],
        model="claude-opus-5", ttl="5m",
    )
    assert messages[0]["content"] == [
        {"type": "text", "text": "only", "cache_control": {"type": "ephemeral"}}
    ]


def test_no_tools_means_no_tools_marker_and_no_crash() -> None:
    tools, system, messages = apply_cache_breakpoints(
        None, _big_text(900), _messages(), model="claude-opus-5", ttl="5m",
    )
    assert tools is None
    assert _markers(system) == [{"type": "ephemeral"}]


# ---------------------------------------------------------------------------
# TTL
# ---------------------------------------------------------------------------

def test_5m_markers_carry_no_ttl_key() -> None:
    """``{"type": "ephemeral"}`` IS the 5-minute form; an explicit ttl would be noise."""
    tools, system, messages = apply_cache_breakpoints(
        _tools(), _big_text(800), _messages(), model="claude-opus-5", ttl="5m",
    )
    for marker in _all_markers(tools, system, messages):
        assert marker == {"type": "ephemeral"}


def test_1h_ttl_is_carried_on_every_marker() -> None:
    tools, system, messages = apply_cache_breakpoints(
        _tools(), _big_text(800), _messages(), model="claude-opus-5", ttl="1h",
    )
    markers = _all_markers(tools, system, messages)
    assert len(markers) == 4
    for marker in markers:
        assert marker == {"type": "ephemeral", "ttl": "1h"}


# ---------------------------------------------------------------------------
# Purity — the caller's own objects are never touched
# ---------------------------------------------------------------------------

def test_the_callers_objects_are_never_mutated() -> None:
    """Pure by contract: the tool loop reuses its ``messages`` list across rounds.

    Mutating it in place would accumulate a marker per round and blow invariant
    I1 open on the second iteration.
    """
    tools_in, messages_in = _tools(), _messages()
    tools_before = copy.deepcopy(tools_in)
    messages_before = copy.deepcopy(messages_in)

    apply_cache_breakpoints(
        tools_in, _big_text(800), messages_in, model="claude-opus-5", ttl="5m",
    )

    assert tools_in == tools_before
    assert messages_in == messages_before


def test_repeated_application_still_yields_four_markers() -> None:
    """I1 under the tool loop's real access pattern — marking round after round."""
    tools, system, messages = _tools(), _big_text(800), _messages()
    for _ in range(3):
        tools, system, messages = apply_cache_breakpoints(
            tools, system, messages, model="claude-opus-5", ttl="5m",
        )
    assert len(_all_markers(tools, system, messages)) == 4


def test_a_growing_conversation_never_accumulates_markers() -> None:
    """I1 under the pattern that actually breaks it — the tool loop.

    ``complete_with_tools`` appends to ONE messages list and re-marks it every
    round. Without stripping, round 2's markers land on the new tail while round
    1's are still sitting on messages that are no longer the tail, and the request
    goes out with six markers. This is the test that catches that; the
    same-length ``test_repeated_application`` above cannot, because re-marking
    the same two positions is idempotent whether stripping happens or not.
    """
    tools, system = _tools(), _big_text(800)
    messages: list[dict[str, Any]] = [{"role": "user", "content": "start"}]
    for round_index in range(4):
        tools, system, messages = apply_cache_breakpoints(
            tools, system, messages, model="claude-opus-5", ttl="5m",
        )
        assert len(_all_markers(tools, system, messages)) <= 4, (
            f"round {round_index} exceeded the four-marker budget"
        )
        # The sharper half: markers must sit on the TAIL, not wherever earlier
        # rounds happened to leave them.
        marked = [i for i, m in enumerate(messages) if _markers(m)]
        assert marked == list(range(len(messages)))[-len(marked):], (
            f"round {round_index} left markers on stale messages: {marked}"
        )
        messages = [*messages, {"role": "assistant", "content": f"reply {round_index}"}]
        messages = [*messages, {"role": "user", "content": f"follow-up {round_index}"}]


def test_the_documented_floor_is_the_smallest_published_minimum() -> None:
    """512 is the floor UNDER the floors — below it nothing caches on any model."""
    assert MIN_CACHEABLE_TOKENS == 512
