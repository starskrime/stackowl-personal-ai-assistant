"""D09.1 — recover a reflection whose only fault is an unescaped quote.

FIVE production diagnostic records, gathered after `describe_parse_failure` shipped
and read from the core's own log:

    json_not_object  chars=691  "Expecting ',' delimiter: line 1 column 310"
    json_not_object  chars=482  "Expecting ',' delimiter: line 1 column 240"
    json_not_object  chars=466  "Unterminated string starting at: line 1 column 13"
    json_not_object  chars=502  (logged before the decoder error was captured)
    json_object      chars=519  keys=["summary"] missing=["suggested_strategy"]

Four of the five are ONE fault: the model wrote a literal ``"`` inside the summary
string without escaping it. Confirmed by reproducing the signatures — an unescaped
quote mid-string yields exactly ``Expecting ',' delimiter``; a raw newline would
yield ``Invalid control character``, which has NOT appeared in production.

So this is a targeted repair, not a general "fix the model's JSON" heuristic. It
runs ONLY after strict parsing has already failed, so it can recover and can never
regress a response that already parsed. The fifth record — a valid object missing
``suggested_strategy`` — is deliberately NOT addressed here: that is a change to the
parser's stated contract and is left alone.

Why this rather than relaxing `required_keys`, which three reviewers proposed: the
relaxation would have recovered ONE of these five. This recovers four.
"""

from __future__ import annotations

import json

from stackowl.memory.reflection_prompt import parse_reflection_response


def _both(raw: str) -> tuple[str, str] | None:
    return parse_reflection_response(raw)


# ---------------------------------------------------------------------------
# The production signatures
# ---------------------------------------------------------------------------

def test_an_unescaped_quote_mid_string_is_recovered() -> None:
    """`Expecting ',' delimiter` — 2 of the 5 live records."""
    raw = '{"summary": "the agent said "hello" to the user", "suggested_strategy": "repeat it"}'
    got = _both(raw)
    assert got is not None, "this is the majority production failure and must recover"
    summary, strategy = got
    assert "hello" in summary
    assert strategy == "repeat it"


def test_an_unterminated_looking_string_is_recovered() -> None:
    """`Unterminated string starting at: line 1 column 13` — 1 of the 5."""
    raw = '{"summary": ""quoted" opener then prose", "suggested_strategy": "s"}'
    got = _both(raw)
    assert got is not None
    assert "opener" in got[0]


def test_several_unescaped_quotes_are_recovered_when_unambiguous() -> None:
    raw = ('{"summary": "it ran "alpha" then "beta" successfully", '
           '"suggested_strategy": "do "alpha" first"}')
    got = _both(raw)
    assert got is not None
    assert "alpha" in got[0] and "beta" in got[0]


def test_a_quote_FOLLOWED_BY_A_COMMA_is_not_recoverable_and_that_is_inherent() -> None:
    """The limit of this repair, pinned so nobody mistakes it for a bug.

    ``{"summary": "it ran "a", then "b" ..."}`` — a quote followed by a comma
    inside a string is byte-for-byte indistinguishable from a string ENDING and
    the next element beginning. No lookahead can separate them; only a parser that
    already knew the intended schema could, and that is a much larger heuristic
    with real regression risk.

    So it is left unrecovered ON PURPOSE. This shape has NOT appeared in any of the
    five measured production failures — all four repairable ones had the quote
    followed by a letter or space — and building for an unobserved case is how a
    repair pass grows into something that corrupts good input. If it ever shows up
    in the log, the diagnostic will name it and this test is where to start.
    """
    raw = '{"summary": "it ran "a", then stopped", "suggested_strategy": "s"}'
    assert _both(raw) is None


# ---------------------------------------------------------------------------
# It must not change anything that already worked
# ---------------------------------------------------------------------------

def test_wellformed_json_is_untouched() -> None:
    raw = json.dumps({"summary": "clean", "suggested_strategy": "also clean"})
    assert _both(raw) == ("clean", "also clean")


def test_properly_escaped_quotes_survive_exactly() -> None:
    """The repair must not double-escape what the model got right."""
    raw = json.dumps({"summary": 'he said "hi"', "suggested_strategy": "s"})
    got = _both(raw)
    assert got == ('he said "hi"', "s"), got


def test_a_fenced_response_still_works() -> None:
    raw = '```json\n{"summary": "s", "suggested_strategy": "t"}\n```'
    assert _both(raw) == ("s", "t")


def test_prose_is_still_rejected() -> None:
    assert _both("I think it went well, honestly.") is None


def test_a_missing_required_key_is_STILL_rejected() -> None:
    """The fifth record's shape. Deliberately unchanged — relaxing the contract is
    a separate decision, and this repair is not a backdoor to it."""
    assert _both(json.dumps({"summary": "only one key"})) is None


def test_empty_and_garbage_never_raise() -> None:
    for bad in ("", "   ", "{", "}", "[]", "null", '{"summary":', "\x00"):
        assert _both(bad) is None, bad
