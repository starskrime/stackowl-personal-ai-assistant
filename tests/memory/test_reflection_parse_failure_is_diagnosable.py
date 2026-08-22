"""D09.1 — a parse failure must say WHY, not show the first 200 characters.

`compute_reflection` logs `raw_preview: result.content[:200]` when
`parse_reflection_response` returns None. Measured 2026-08-22 over the retained
8-day window: **50 of 51 failure records carry a preview of exactly 200 characters**
— every single one truncated at the cap — and 49 of them show `{"summary": "..."`
cut off mid-sentence.

That looks like a root cause and is not one. `parse_reflection_response` requires
BOTH `summary` and `suggested_strategy`, and a reflection's `summary` alone runs
well past 200 characters, so the second key is simply beyond the window. Every
well-formed response and every malformed one look identical in this field.

A panel lens read those 50 previews as proof that `suggested_strategy` was absent
and proposed relaxing `required_keys`. On this evidence that is unsupported: the
field cannot distinguish "key missing" from "key present, past the cap". It is the
same defect as `D05.8`'s `dropped[:20]` — a truncated field read as a complete
answer — and the fix is the same: make the record answer the question.

So this pins the diagnostic, not a root cause. Once the key names are logged, ONE
query settles which branch actually fires, and the real fix can be chosen on
evidence instead of on the shape of a prefix.
"""

from __future__ import annotations

import json
import logging

from stackowl.memory.reflection_prompt import parse_reflection_response


def _fields(record: logging.LogRecord) -> dict[str, object]:
    return dict(getattr(record, "_fields", {}) or {})


# ---------------------------------------------------------------------------
# The parser's own contract, pinned so a later "relax it" change is deliberate
# ---------------------------------------------------------------------------

def test_both_keys_parse() -> None:
    assert parse_reflection_response(
        json.dumps({"summary": "X worked", "suggested_strategy": "do X"})
    ) == ("X worked", "do X")


def test_a_missing_suggested_strategy_is_rejected_today() -> None:
    """Current behaviour, and the docstring agrees: both keys are required.

    Pinned so that relaxing it later is a decision someone makes on purpose,
    with the evidence this test's sibling instrumentation exists to collect.
    """
    assert parse_reflection_response(json.dumps({"summary": "X worked"})) is None


def test_a_present_but_non_string_suggested_strategy_is_TOLERATED() -> None:
    """The tolerance at `reflection_prompt.py:102-103` is REACHABLE.

    A lens reported it as dead code. It is not: `required_keys` checks only that
    the key is PRESENT, so a JSON `null` passes that gate and then falls into the
    `not isinstance(suggested, str)` branch. Recorded as a test because "dead
    code" was asserted about a live path.
    """
    assert parse_reflection_response(
        json.dumps({"summary": "X worked", "suggested_strategy": None})
    ) == ("X worked", "")


# ---------------------------------------------------------------------------
# The diagnostic itself
# ---------------------------------------------------------------------------

def test_the_failure_record_names_the_keys_it_saw(caplog: object) -> None:
    """A 200-char prefix cannot tell a missing key from one past the cap.

    The record must carry the top-level key names, so one query separates
    "the model omitted a key" from "the model returned prose" from "the JSON was
    truncated" — none of which the preview can distinguish today.
    """
    from stackowl.memory.reflection_prompt import describe_parse_failure

    long_summary = "y" * 400
    payload = json.dumps({"summary": long_summary})

    shape = describe_parse_failure(payload)
    assert shape["keys"] == ["summary"], shape
    assert shape["missing"] == ["suggested_strategy"], shape
    assert shape["shape"] == "json_object"


def test_prose_and_fences_are_distinguished_from_a_key_problem() -> None:
    from stackowl.memory.reflection_prompt import describe_parse_failure

    assert describe_parse_failure("I think the agent did well.")["shape"] == "not_json"
    assert describe_parse_failure("")["shape"] == "empty"
    fenced = describe_parse_failure(
        '```json\n{"summary": "s", "suggested_strategy": "t"}\n```'
    )
    assert fenced["shape"] == "json_object"
    assert fenced["missing"] == []


def test_a_truncated_object_is_named_as_truncated_not_as_a_missing_key() -> None:
    """The case the 200-char preview makes indistinguishable, and the reason
    relaxing `required_keys` on preview evidence would have been a guess."""
    from stackowl.memory.reflection_prompt import describe_parse_failure

    shape = describe_parse_failure('{"summary": "the model ran out of tok')
    assert shape["shape"] == "json_truncated", shape
    assert shape["missing"] is None, "a truncated object cannot report missing keys"


def test_describe_never_raises_on_anything() -> None:
    """It runs inside a failure handler. It must not be able to add a second one."""
    from stackowl.memory.reflection_prompt import describe_parse_failure

    for bad in ("", "   ", "{", "[]", "null", "{'python': 'dict'}", "\x00\xff"):
        got = describe_parse_failure(bad)
        assert isinstance(got, dict) and "shape" in got, (bad, got)
