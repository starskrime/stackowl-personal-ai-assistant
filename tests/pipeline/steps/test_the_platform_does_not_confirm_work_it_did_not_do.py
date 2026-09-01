""""Fixed." is a claim about work done, and may only be said when work was done.

THE REPORT. Bakir, 2026-09-01, quoting his own chat: "Why platform repeats:
Fixed. From now on here: no asterisks, no raw tables and replies kept short. 714
in / 33 out". He had already been told this. Twice. Verbatim.

MEASURED THE SAME DAY across every ``negative/format captured`` record — five in
total, and the last two are the report:

    22:20:15  changes={length: terse, tables: off}                 defects=[]
    22:25:40  changes={markdown: minimal}                          defects=['asterisks']
    00:56:46  changes={markdown: minimal}                          defects=['asterisks']
    02:32:05  changes={tables: off, markdown: minimal, length: terse}  defects=[]
    02:32:24  changes={tables: off, markdown: minimal, length: terse}  defects=[]

NINETEEN SECONDS APART, both with ``defects=[]`` and ``changes`` equal to the
ENTIRE already-stored style. Nothing was detected as wrong with the last render
— correctly, the formatter had been repaired hours earlier — and nothing was
written that was not already there. The reply still said "Fixed."

THE CHAIN, ALL FIVE LINKS MEASURED:

1. the message classifies negative/format;
2. ``_detect_defects`` finds nothing wrong (defects=[]);
3. the no-defect branch falls back to ``changes = _explicit_fields(current)`` —
   re-asserting the style ALREADY stored;
4. ``_merge_write_style`` writes identical values: a no-op;
5. ``_negative_confirmation([], style)`` returned the literal ``"Fixed."``.

AND IT SHORT-CIRCUITED THE TURN, so what he actually asked was never answered —
which is why he asked again, which classified negative again, which produced the
same receipt. Self-sustaining, and the 714-in/33-out turn he pasted is its
signature: almost no output, because there was nothing to say.

THE ONE BRANCH THAT WOULD HAVE ASKED HIM was already dead. ``_CLARIFY_QUESTION``
is guarded by ``if not changes``, and ``changes`` is non-empty whenever a style
exists — so it is unreachable for anyone who has ever set one. Dead code for
exactly the people who complain most.

THE FIX IS THE HONESTY RULE APPLIED TO THE ONE FUNCTION WHOSE JOB IS TO REPORT
ON A CORRECTION. The writer now says whether anything moved; "fixed" is reserved
for a detected defect, "Noted" for a real preference change, and when neither
happened the turn is ANSWERED instead of confirmed.
"""

from __future__ import annotations

import json

import pytest

from stackowl.channels._format import OUTPUT_STYLE_KEY, OutputStyle
from stackowl.pipeline.steps.feedback import (
    _merge_write_style,
    _negative_confirmation,
)

# Only the store round-trips are async; the wording tests are pure.
_async = pytest.mark.asyncio

_BAKIRS_STYLE = {"markdown": "minimal", "tables": "off", "length": "terse"}


class _Store:
    """Minimal PreferenceStore stand-in — get/set over one dict."""

    def __init__(self, initial: dict[str, str] | None = None) -> None:
        self.data = dict(initial or {})
        self.writes = 0

    async def get(self, owner_key: str, key: str) -> str | None:
        return self.data.get(f"{owner_key}:{key}")

    async def set(self, owner_key: str, key: str, value: str) -> None:
        self.writes += 1
        self.data[f"{owner_key}:{key}"] = value


# --------------------------------------------------------------------------- #
# The writer must say whether anything actually moved                          #
# --------------------------------------------------------------------------- #


@_async
async def test_re_asserting_the_stored_style_reports_no_change() -> None:
    """The exact 02:32 shape: changes == the whole style that is already stored."""
    store = _Store({f"owner:{OUTPUT_STYLE_KEY}": json.dumps(_BAKIRS_STYLE)})
    _style, changed = await _merge_write_style(store, "owner", dict(_BAKIRS_STYLE))
    assert changed is False, (
        "a re-assertion reported as a change is what let the platform answer "
        "'Fixed.' twice in nineteen seconds"
    )


@_async
async def test_a_real_change_reports_a_change() -> None:
    """The expensive direction — a wrong False here would silence a genuine
    correction and the user would never be told it was recorded."""
    store = _Store({f"owner:{OUTPUT_STYLE_KEY}": json.dumps({"markdown": "minimal"})})
    _style, changed = await _merge_write_style(store, "owner", {"tables": "off"})
    assert changed is True


@_async
async def test_the_first_ever_preference_is_a_change() -> None:
    store = _Store()
    style, changed = await _merge_write_style(store, "owner", {"markdown": "minimal"})
    assert changed is True
    assert style.markdown == "minimal"


@_async
async def test_a_corrupt_prior_record_is_a_change_not_a_crash() -> None:
    """Overwriting corruption IS a change, and must be reported as one rather
    than compared against garbage."""
    store = _Store({f"owner:{OUTPUT_STYLE_KEY}": "{not json"})
    _style, changed = await _merge_write_style(store, "owner", {"markdown": "minimal"})
    assert changed is True


@_async
async def test_the_style_is_still_written_even_when_unchanged() -> None:
    """Durability is why the re-assertion exists at all; the fix removes the
    CLAIM, not the write."""
    store = _Store({f"owner:{OUTPUT_STYLE_KEY}": json.dumps(_BAKIRS_STYLE)})
    await _merge_write_style(store, "owner", dict(_BAKIRS_STYLE))
    assert store.writes == 1


# --------------------------------------------------------------------------- #
# The wording may not claim a repair                                           #
# --------------------------------------------------------------------------- #


def test_a_detected_defect_still_says_fixed() -> None:
    """Because that one IS true — a transform was at fault and was corrected."""
    message = _negative_confirmation(["asterisks"], OutputStyle(markdown="minimal"))
    assert "fixed" in message.lower()
    assert "asterisks" in message


def test_no_detected_defect_never_says_fixed() -> None:
    """The literal message Bakir received twice."""
    message = _negative_confirmation([], OutputStyle(**_BAKIRS_STYLE))
    assert not message.lower().startswith("fixed"), (
        "the platform is claiming a repair for a turn in which nothing was "
        "detected and nothing was written"
    )
    assert message.startswith("Noted")


def test_the_rules_are_still_stated_so_the_reply_is_useful() -> None:
    message = _negative_confirmation([], OutputStyle(**_BAKIRS_STYLE))
    assert "no asterisks" in message and "replies kept short" in message


def test_there_is_no_branch_that_confirms_nothing() -> None:
    """Structural. A third branch for "no defect and no change" would be the
    receipt this item exists to remove — the caller answers the user instead."""
    import inspect

    from stackowl.pipeline.steps import feedback

    source = inspect.getsource(feedback._negative_confirmation)
    assert source.count("return ") == 2, (
        "a third confirmation branch is back — when nothing was detected and "
        "nothing moved there is nothing to confirm"
    )


def test_the_caller_answers_instead_of_confirming() -> None:
    """The loop-breaker, pinned over the source: without the fall-through the
    turn short-circuits and the user's actual question is never answered, which
    is what made him ask again and get the identical receipt."""
    import inspect

    from stackowl.pipeline.steps import feedback

    source = inspect.getsource(feedback._handle_negative)
    assert "if not defects and not changed:" in source, (
        "the no-defect no-change path no longer falls through — the "
        "self-sustaining confirmation loop is back"
    )
    assert source.index("if not defects and not changed:") < source.index(
        "_short_circuit(state, _negative_confirmation"
    ), "the fall-through must come BEFORE the confirmation, or it never runs"
