"""A reflection with a good summary was being discarded for its second key.

MEASURED 2026-08-31 across the retained logs: **64** records of
``[reflection] compute_reflection: could not parse response — skipping`` against
202 reflection batches, and the structured failure fields (which D09.1 added for
exactly this question) split them cleanly::

    shape json_object      15   keys ['summary']   missing ['suggested_strategy']
    shape json_not_object  49   of which 35 "Unterminated string starting at ..."

    chars: min 355  p50 525  p90 621  max 746

TWO CAUSES, ONE CONSEQUENCE: a summary the model wrote well is thrown away whole.

CAUSE 1 — THE FUNCTION CONTRADICTS ITSELF. ``parse_reflection_response`` gates on
``required_keys=REFLECTION_REQUIRED_KEYS`` (both keys), and then, two lines later::

    if not isinstance(suggested, str):
        suggested = ""

It demands the key and then handles its absence. And BOTH readers already treat it
as optional — ``classify.py:284`` writes the strategy line only ``if
r.suggested_strategy``, and ``reflection_writer_handler.py:424`` appends "Repeat:"
only ``if suggested_strategy``. The parser was stricter than everything that reads
its output. That is 15 reflections.

CAUSE 2 — THE MODEL STOPS MID-SENTENCE. Not a cap of ours: output tokens on those
traces run 2 to 2,912 with p50 183, so nothing is pinned at a ceiling, and
``_output_cap`` is window-sized rather than fixed. The model simply stops. 35
responses end inside the summary string.

THE REPAIR SLOT ALREADY EXISTS AND ITS RULE IS ALREADY WRITTEN DOWN.
``_escape_stray_quotes`` runs "ONE RETRY, and only after strict parsing has failed
... a response that already parses never reaches the repair, so this can recover
and cannot regress". This is a second repair in the same slot, under the same rule.

AND IT REFUSES A FRAGMENT. A summary cut mid-clause can invert its own meaning —
"the agent did not" — and it is injected into later prompts. So the recovered text
is trimmed back to its last COMPLETE SENTENCE, and a response with no complete
sentence is still discarded. Recovering less is the point.
"""

from __future__ import annotations

import logging

import pytest

from stackowl.memory.reflection_prompt import parse_reflection_response

#: Verbatim shapes from the live failure records, 2026-08-31.
_TRUNCATED = (
    '{"summary": "The RCA succeeded by treating the repeated unachieved_effect '
    "outcomes as a missing state-verification loop. It cited the grant-to-existing-owl "
    'inputs, repeated identical owl_build calls, an'
)
_OBJECT_MISSING_STRATEGY = (
    '{"summary": "The verifier succeeded by splitting the evidence brief into '
    "concretely recorded facts and inferred mechanisms, then rejecting the "
    'budget-exhaustion root cause because the brief lacked stored evidence."}'
)


def test_a_valid_object_with_ONLY_a_summary_is_kept() -> None:
    """The 15. Both readers already guard on an empty strategy."""
    parsed = parse_reflection_response(_OBJECT_MISSING_STRATEGY)

    assert parsed is not None, "a complete summary was discarded for a missing key"
    summary, strategy = parsed
    assert summary.startswith("The verifier succeeded")
    assert strategy == ""


def test_both_keys_still_come_through_when_the_model_sends_both() -> None:
    parsed = parse_reflection_response(
        '{"summary": "It worked.", "suggested_strategy": "Do it again."}'
    )
    assert parsed == ("It worked.", "Do it again.")


def test_a_TRUNCATED_response_keeps_its_completed_sentences() -> None:
    """The 35. The model stopped mid-clause; everything before the last full stop
    is still what it observed."""
    parsed = parse_reflection_response(_TRUNCATED)

    assert parsed is not None, "35 of 64 failures look exactly like this"
    summary, strategy = parsed
    assert summary.endswith("."), "the recovered text must end on a sentence"
    assert "state-verification loop" in summary
    assert "grant-to-existing-owl" not in summary, (
        "the trailing fragment was kept — it can invert its own meaning and it is "
        "injected into later prompts"
    )
    assert strategy == ""


def test_a_fragment_with_NO_complete_sentence_is_still_discarded() -> None:
    """Recovering less is the point. There is nothing trustworthy here."""
    assert parse_reflection_response('{"summary": "The agent did not') is None


def test_an_EMPTY_summary_is_still_discarded() -> None:
    assert parse_reflection_response('{"summary": ""}') is None
    assert parse_reflection_response('{"summary": "   "}') is None


def test_PROSE_is_still_discarded() -> None:
    """The repair must not turn "the model ignored the format" into a reflection."""
    assert parse_reflection_response("I think the agent did well overall.") is None
    assert parse_reflection_response("") is None


def test_a_response_that_ALREADY_PARSES_never_reaches_the_repair() -> None:
    """The rule the existing repair states and this one inherits: it can recover
    and cannot regress."""
    good = '{"summary": "One. Two. Three.", "suggested_strategy": "S"}'
    assert parse_reflection_response(good) == ("One. Two. Three.", "S")


def test_the_stray_quote_repair_still_works() -> None:
    """Four of five failures once shared that fault; extending the slot must not
    displace what already lives in it."""
    raw = (
        '{"summary": "The agent called "shell" and it worked.", '
        '"suggested_strategy": "Keep going."}'
    )
    parsed = parse_reflection_response(raw)
    assert parsed is not None
    assert "shell" in parsed[0]


def test_a_recovery_is_announced_at_INFO(caplog: pytest.LogCaptureFixture) -> None:
    """Production runs at INFO, and this is the only line that could ever show the
    repair is doing anything."""
    with caplog.at_level(logging.INFO):
        parse_reflection_response(_TRUNCATED)

    records = [r for r in caplog.records if "truncat" in r.getMessage().lower()]
    assert records, "a silent repair cannot be measured, confirmed or doubted"
    fields = getattr(records[-1], "_fields", {})
    assert fields.get("recovered_chars")
    assert fields.get("dropped_chars")
