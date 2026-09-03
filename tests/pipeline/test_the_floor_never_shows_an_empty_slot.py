"""The honest floor must never hand the user a sentence with nothing in it.

WHAT BAKIR ACTUALLY RECEIVED (live, from task_outcomes):

    "I couldn't fully complete this: how many west world sessason exists ? it is
     a series. The capability that failed: . What I tried: .  Technical detail: "

Three empty slots. The message is mostly punctuation. He asked a question and
was told, at length, nothing.

THE ROOT CAUSE IS COMBINATORIAL, NOT COSMETIC. ``synthesize_floor`` chooses ONE
of three PRE-BAKED whole-message templates — ``self_heal_floor_graceful`` (all
empty), ``self_heal_floor_unattributed`` (attempts but no capability and no
error) and ``self_heal_floor`` (everything). But the data has 2**3 = 8 shapes:
capability present or not, attempts present or not, error present or not. FIVE
of the eight have no template of their own, so they fall through to the five-slot
one, which renders every sentence whether or not its slot has anything in it.

MEASURED 2026-09-03 by running all eight through the real function: (cap, att,
err) of 001, 011, 100, 101 and 110 each rendered at least one blank slot. Only
000, 010 and 111 were clean.

MEASURED IN PRODUCTION: 1,644 of the 1,794 floor messages ever recorded contain
an empty slot. Restricting to AFTER the 2026-08-15 repairs — which added the
graceful and unattributed templates, i.e. two more pre-baked shapes — 81 of 133
(61%) STILL render at least one blank, the most recent on 2026-09-01. Adding a
template per shape is what produced this; the eighth template would not fix the
ninth case.

SO THE FIX IS TO STOP CHOOSING A SHAPE. The message is composed from the
sentences whose data is actually present. There is no combination left to miss,
because nothing is rendered unless it has something to say.

THE SILENT FAILURE THIS OPENS, AND WHY IT IS GUARDED HERE. ``localize`` falls
back to English and then, if the key is missing everywhere, RETURNS THE KEY
ITSELF (setup/localize.py:208-216). A typo'd or untranslated sentence key would
therefore print the literal string ``self_heal_floor_s_capability`` into a user's
message, and nothing would raise. Hence the key-coverage tripwire below: it is
the one that notices.
"""

from __future__ import annotations

from itertools import product

import pytest

from stackowl.pipeline.supervisor import synthesize_floor
from stackowl.setup.localize import localize

#: Every language the floor ships in.
LANGS = ("en", "de", "fr", "es")

#: The per-sentence keys the composed floor is built from.
SENTENCE_KEYS = (
    "self_heal_floor_s_goal",
    "self_heal_floor_s_capability",
    "self_heal_floor_s_attempts",
    "self_heal_floor_s_error",
)


def _blank_slot(text: str) -> str | None:
    """Return a description of the first empty-slot artefact, or None if clean.

    An empty slot always leaves one of these behind: a label followed straight by
    the sentence-ending period, a label with nothing after it at the end of the
    message, or the doubled space where an omitted clause used to sit.
    """
    if ": ." in text:
        return "a label followed immediately by a full stop"
    if text.rstrip().endswith(":"):
        return "a label with nothing after it at the end"
    if "  " in text:
        return "a doubled space where an empty clause was rendered"
    return None


# --------------------------------------------------------------------------- #
# The eight shapes                                                             #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(("cap", "att", "err"), list(product([0, 1], repeat=3)))
@pytest.mark.parametrize("lang", LANGS)
def test_no_combination_of_available_data_renders_an_empty_slot(
    cap: int, att: int, err: int, lang: str,
) -> None:
    """All 2**3 shapes, in all four languages. Five of the eight were broken."""
    out = synthesize_floor(
        "find the release date",
        "HTTP 500" if err else None,
        ["web_fetch", "web_search"] if att else [],
        None,
        failed_capability="web_fetch" if cap else "",
        lang=lang,
    )
    problem = _blank_slot(out)
    assert problem is None, f"({cap},{att},{err}) {lang}: {problem} — {out!r}"


@pytest.mark.parametrize(("cap", "att", "err"), list(product([0, 1], repeat=3)))
def test_a_sentence_appears_only_when_it_has_something_to_say(
    cap: int, att: int, err: int,
) -> None:
    """The invariant behind the fix: presence of the sentence == presence of data.

    Guards both directions. Omitting a sentence that HAS data would hide the
    technical detail the floor exists to be honest about; rendering one that does
    not is the defect."""
    out = synthesize_floor(
        "find the release date",
        "HTTP 500" if err else None,
        ["web_fetch", "web_search"] if att else [],
        None,
        failed_capability="web_fetch" if cap else "",
    )
    assert ("The capability that failed" in out) == bool(cap)
    assert ("What I tried" in out) == bool(att)
    assert ("Technical detail" in out) == bool(err)


def test_the_partial_answer_is_never_dropped() -> None:
    """A partial is the only part of the message with real content in it."""
    out = synthesize_floor(
        "find the release date", None, [], "I found two of the four dates",
        failed_capability="",
    )
    assert "I found two of the four dates" in out


# --------------------------------------------------------------------------- #
# The three shapes that already worked must not change                         #
# --------------------------------------------------------------------------- #


def test_nothing_at_all_still_gets_the_warm_message() -> None:
    """(0,0,0) went to self_heal_floor_graceful and must continue to."""
    out = synthesize_floor("find it", None, [], None, failed_capability="")
    assert "capability that failed" not in out.lower()
    assert "tangled up" in out


def test_attempts_without_a_culprit_still_says_so_explicitly() -> None:
    """(0,1,0) — naming attempts[0] would accuse a tool that may have SUCCEEDED,
    so the floor says plainly that it cannot attribute the failure."""
    out = synthesize_floor("find it", None, ["web_fetch"], None, failed_capability="")
    assert "No single step reported a failure" in out
    assert "The capability that failed" not in out


def test_a_fully_attributed_failure_still_carries_all_three_sentences() -> None:
    """(1,1,1) — the shape that already worked."""
    out = synthesize_floor(
        "find it", "HTTP 500", ["web_fetch"], None, failed_capability="web_fetch",
    )
    assert "The capability that failed: web_fetch." in out
    assert "What I tried: web_fetch." in out
    assert "Technical detail: HTTP 500" in out


def test_the_floor_is_never_empty_on_any_shape() -> None:
    """TerminalResponseGuarantee — the whole point of this function."""
    for cap, att, err in product([0, 1], repeat=3):
        for lang in LANGS:
            out = synthesize_floor(
                "g", "e" if err else None, ["t"] if att else [], None,
                failed_capability="c" if cap else "", lang=lang,
            )
            assert out.strip(), f"empty floor for ({cap},{att},{err}) {lang}"


# --------------------------------------------------------------------------- #
# The silent failure: a missing key prints its own name                        #
# --------------------------------------------------------------------------- #


@pytest.mark.tripwire
def test_every_sentence_key_exists_in_every_language() -> None:
    """``localize`` returns the KEY ITSELF when a string is missing, so an
    untranslated sentence would print "self_heal_floor_s_capability" into a
    user's message and nothing would raise."""
    for key in SENTENCE_KEYS:
        for lang in LANGS:
            assert localize(key, lang) != key, (
                f"{key} is missing for {lang!r} — the floor would print the key name"
            )


@pytest.mark.tripwire
def test_no_rendered_floor_ever_leaks_a_localization_key() -> None:
    """The end-to-end version of the guard above, across every shape."""
    for cap, att, err in product([0, 1], repeat=3):
        for lang in LANGS:
            out = synthesize_floor(
                "g", "e" if err else None, ["t"] if att else [], None,
                failed_capability="c" if cap else "", lang=lang,
            )
            assert "self_heal_floor" not in out, f"key leaked: {out!r}"


# --------------------------------------------------------------------------- #
# The full cross-product, including the two shapes I missed myself             #
# --------------------------------------------------------------------------- #


@pytest.mark.tripwire
def test_the_whole_cross_product_is_clean() -> None:
    """goal x capability x attempts x error x partial x lang x lean.

    The narrower version of this test passed while TWO defects were live, because
    it always supplied a goal and never exercised lean:

    * ``delivery_gate`` passes ``strip_turn_context(state.input_text)``, which can
      strip to "" — and the goal-bearing lead then rendered "I couldn't fully
      complete this: .", a blank slot introduced by the very fix meant to remove
      them;
    * the lean suffix was glued onto a dangling "Technical detail:" so it READ as
      the technical detail.
    """
    for goal, cap, att, err, part, lang, lean in product(
        ["find it", ""], [0, 1], [0, 1], [0, 1], ["", "I got two of four"],
        LANGS, [False, True],
    ):
        out = synthesize_floor(
            goal, "HTTP 500" if err else None, ["web_fetch"] if att else [],
            part or None, failed_capability="web_fetch" if cap else "",
            lang=lang, lean=lean,
        )
        shape = f"goal={bool(goal)} cap={cap} att={att} err={err} part={bool(part)} {lang} lean={lean}"
        assert out.strip(), f"empty floor: {shape}"
        problem = _blank_slot(out)
        assert problem is None, f"{shape}: {problem} — {out!r}"
        assert "{" not in out and "}" not in out, f"{shape}: raw template — {out!r}"
        assert "self_heal_floor" not in out, f"{shape}: key leaked — {out!r}"


@pytest.mark.tripwire
def test_a_non_english_floor_never_mixes_in_an_english_sentence() -> None:
    """``localize`` falls back to English silently when a key is missing for a
    language, so one untranslated sentence produces a German message with an
    English clause in it and nothing raises."""
    english_marks = (
        "The capability that failed", "What I tried", "Technical detail",
        "No single step reported", "I couldn't fully complete",
    )
    for lang in ("de", "fr", "es"):
        for cap, att, err in product([0, 1], repeat=3):
            out = synthesize_floor(
                "find it", "HTTP 500" if err else None,
                ["web_fetch"] if att else [], None,
                failed_capability="web_fetch" if cap else "", lang=lang,
            )
            for mark in english_marks:
                assert mark not in out, (
                    f"{lang} floor fell back to English for {mark!r}: {out!r}"
                )


def test_the_lean_note_reaches_every_shape_including_graceful() -> None:
    """The graceful message used to be an EARLY RETURN placed before the lean
    suffix was appended, so a small-model graceful floor never carried the note
    explaining why the turn struggled. Nothing noticed: the only lean test
    asserts the suffix is ABSENT for a normal window."""
    suffix = localize("self_heal_floor_lean_suffix", "en")
    assert suffix
    for cap, att, err in product([0, 1], repeat=3):
        out = synthesize_floor(
            "find it", "HTTP 500" if err else None, ["web_fetch"] if att else [],
            None, failed_capability="web_fetch" if cap else "", lean=True,
        )
        assert suffix in out, f"lean note missing for ({cap},{att},{err}): {out!r}"
