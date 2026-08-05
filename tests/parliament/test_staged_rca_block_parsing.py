"""ADR-19 Loop 2 — why 64% of RCAs produced no verdict.

MEASURED over 15 days of production logs:
  1,131 incidents opened -> 1,103 RCAs run -> 725 "no verdict"
    410  "missing root_cause/fix"  — and in 409 of them BOTH fields were missing
    315  "a stage failed"          — all TimeoutError, all before 2026-07-27
                                      (DEBT-18 raised the stage budget; fixed)

"Both missing" is the tell. One field omitted is a model being sloppy; both
missing every time is a structure nobody could read. The old pattern required
the key at column zero and bare (`^ROOT_CAUSE:`) and rejected `**ROOT_CAUSE:**`
— the most common way a model emits a labelled field.

Fixed in the PARSER, not the prompt: making a model comply is a per-model
negotiation that a weaker or swapped backend re-opens; making the reader
tolerant is done once.
"""

from __future__ import annotations

import pytest

from stackowl.parliament.staged_rca import _block_field

_VALUE = "the pool was closed"


@pytest.mark.parametrize(
    ("shape", "text"),
    [
        ("bare",              f"ROOT_CAUSE: {_VALUE}"),
        ("markdown bold",     f"**ROOT_CAUSE:** {_VALUE}"),
        ("bold outside colon", f"**ROOT_CAUSE**: {_VALUE}"),
        ("list bullet",       f"- ROOT_CAUSE: {_VALUE}"),
        ("list asterisk",     f"* ROOT_CAUSE: {_VALUE}"),
        ("numbered",          f"1. ROOT_CAUSE: {_VALUE}"),
        ("numbered paren",    f"2) ROOT_CAUSE: {_VALUE}"),
        ("heading",           f"## ROOT_CAUSE: {_VALUE}"),
        ("indented",          f"    ROOT_CAUSE: {_VALUE}"),
        ("code fence",        f"```\nROOT_CAUSE: {_VALUE}\n```"),
        ("lowercase key",     f"root_cause: {_VALUE}"),
    ],
)
def test_every_realistic_model_shape_parses(shape, text):
    """Eight of these were REJECTED before. Each rejection was a whole RCA
    discarded — the platform's learning-from-incidents path, silently barren."""
    assert _block_field(text, "ROOT_CAUSE") == _VALUE, shape


def test_the_bare_form_is_unchanged():
    """The previously-working shape must parse identically — this is a widening,
    not a replacement."""
    assert _block_field("ROOT_CAUSE: x\nFIX: y", "ROOT_CAUSE") == "x"
    assert _block_field("ROOT_CAUSE: x\nFIX: y", "FIX") == "y"


def test_a_decorated_following_key_still_TERMINATES_the_value():
    """The trap in widening this: if the terminator lookahead is not widened the
    same way, the first field swallows the whole document and FIX comes back
    empty — turning one parse bug into a worse one."""
    text = "**ROOT_CAUSE:** the cause\nmore detail\n**FIX:** the fix\n**SKILL_NAME:** n"
    assert _block_field(text, "ROOT_CAUSE") == "the cause\nmore detail"
    assert _block_field(text, "FIX") == "the fix"
    assert _block_field(text, "SKILL_NAME") == "n"


def test_multiline_values_survive():
    assert _block_field("ROOT_CAUSE: a\nb\nc\nFIX: d", "ROOT_CAUSE") == "a\nb\nc"


def test_an_absent_key_is_still_None():
    """The guard the caller depends on to refuse a verdict honestly."""
    assert _block_field("SOMETHING_ELSE: x", "ROOT_CAUSE") is None


def test_an_empty_value_is_None_not_empty_string():
    """A key with nothing after it must not read as a verdict."""
    assert _block_field("ROOT_CAUSE:\nFIX: b", "ROOT_CAUSE") is None
    assert _block_field("**ROOT_CAUSE:**\n**FIX:** b", "ROOT_CAUSE") is None


def test_prose_mentioning_the_key_is_not_matched_mid_line():
    """Tolerance must not become 'match anything'. A key referenced inside a
    sentence is not a field."""
    assert _block_field("we think the ROOT_CAUSE: is unclear", "ROOT_CAUSE") is None


def test_trailing_fence_and_emphasis_are_stripped_from_the_value():
    """Otherwise the root cause carries ``` into the authored skill body."""
    assert _block_field("```\nROOT_CAUSE: x\n```", "ROOT_CAUSE") == "x"
    assert _block_field("**ROOT_CAUSE:** x**", "ROOT_CAUSE") == "x"


def test_the_widening_is_structural_not_vocabulary():
    """Standing rule: no hardcoded English. Asserted BEHAVIOURALLY rather than by
    grepping the source — a source grep here would only read my own comments.

    If the parser had learned English cues, a key made of non-Latin characters
    would not parse. It does, because the only things widened are bullets,
    heading hashes and emphasis marks.
    """
    assert _block_field("**ПРИЧИНА:** значение", "ПРИЧИНА") == "значение"
    assert _block_field("- 原因: 値", "原因") == "値"
