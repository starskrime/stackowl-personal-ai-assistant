"""`Last verified` is a claim; this makes it one reality can settle.

WHY THIS EXISTS. `DOC_STANDARD` requires every design document to carry
``Last verified: YYYY-MM-DD, against commit <sha>`` beside a ``Source:``. That
pairing makes staleness CHECKABLE — and nothing checked it, so the claim aged
exactly as an escalation's premise aged before `premise_check` and a `partial`
stage's evidence aged before `closing_check`. Both were cured by making the claim
executable; `scripts/doc_check.py` is the same cure for the third instance.

MEASURED 2026-09-06 over 84 documents: **15 stale**, their cited sources last
changed AFTER the date the document claims verification — D01.1 and D01.7 by six
weeks.

THE PARSER IS THE PART THAT WAS WRONG, TWICE, AND IS WHY THIS FILE EXISTS.
A first pass searched the WHOLE TEXT for "Last verified" and reported 83 of 84
documents compliant; the header parser reported 35. Both numbers were right about
different questions, and only one is about DOC_STANDARD. The disagreement was
resolved by reading the outlier: `BROWSER-BACKENDS.md` uses `**Status.**` prose
rather than the prescribed `> **Status:**` blockquote, so the phrase appears in
its body while the header is absent. Structure, not substring.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "scripts"))

from doc_check import _header  # noqa: E402


class TestTheHeaderIsReadStructurally:
    def test_it_reads_the_prescribed_blockquote(self) -> None:
        head = _header(
            "# Thing\n\n"
            "> **Status:** live\n"
            "> **Source:** `src/stackowl/a.py`\n"
            "> **Last verified:** 2026-09-06, against commit `abc1234`\n\n"
            "## Why this exists\n"
        )

        assert head["Status"] == "live"
        assert head["Last verified"].startswith("2026-09-06")
        assert "a.py" in head["Source"]

    def test_a_BODY_mention_is_still_not_a_header(self) -> None:
        """THE DISAGREEMENT THAT SENT THIS BACK FOR A SECOND MEASUREMENT.

        A whole-text search for "Last verified" called 83 of 84 documents
        compliant; the structural parser said 35. Both were right about different
        questions and only one is about DOC_STANDARD. This is the property that
        settled it, and it is untouched: a phrase in the BODY is not a field.
        """
        head = _header(
            "# Thing\n\n"
            "Some prose that mentions Last verified: 2026-01-01 in passing.\n"
        )

        assert head == {}, f"a body mention was read as a header field: {head}"

    def test_a_PROSE_FIELD_at_the_top_is_read_and_its_body_is_not(self) -> None:
        """THE DECISION REFINED, 2026-09-08, and the refinement is the point.

        This assertion used to require `_header` to return `{}` for the
        `BROWSER-BACKENDS.md` shape. That was right about the HAZARD — prose must
        not confer compliance — and wrong about the CORPUS: twenty design
        documents open with `**Item.** …` / `**Last verified.** 2026-09-05,
        commit `…`` and the report called every one of them undated. See
        DEBT-234; "unmeasurable" was a claim about the corpus and a fact about the
        parser.

        Every property the original decision protects survives. A body mention
        still yields `{}` (above). The prescribed blockquote still wins. A field
        below the first heading is still ignored. And BROWSER-BACKENDS is STILL
        unmeasurable, because reading its `**Status.**` gives it no verification
        date — the outcome the decision exists to produce is unchanged.

        What changed is only that a field-shaped line at the TOP is now read as
        one, and the sentence AFTER a blank line is not swallowed into it.
        """
        head = _header(
            "# Browser backends\n\n"
            "**Status.** Design, not built.\n\n"
            "Some prose that happens to mention Last verified in passing.\n"
        )

        assert head == {"Status": "Design, not built."}, head
        assert "Last verified" not in head, (
            "the trailing prose was swallowed into the header, which is the "
            "substring-compliance failure this class exists to prevent"
        )

    def test_the_header_stops_at_the_body(self) -> None:
        """A `> **Field:**` line further down the document is not the header —
        otherwise a quoted example inside a section would silently redefine it."""
        head = _header(
            "# Thing\n\n"
            "> **Status:** live\n\n"
            "## Model\n\n"
            "> **Status:** an example being quoted, not this document's status\n"
        )

        assert head == {"Status": "live"}, head


class TestItReportsTwoPopulationsAndNeverConflatesThem:
    def test_the_script_runs_and_names_what_it_could_not_measure(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The cadence-sweep lesson: a count of "stale" is worthless if dozens of
        documents were silently unmeasurable. Both numbers, and the unmeasurable
        ones BY NAME, or the reader cannot tell what the number is made of."""
        import doc_check

        assert doc_check.main() == 0
        out = capsys.readouterr().out

        assert "STALE" in out and "unmeasurable" in out
        assert "UNMEASURABLE" in out, "the unmeasurable set is counted but never named"
        assert ".md" in out.split("UNMEASURABLE")[1], "named by count only, not by file"

    def test_it_sees_a_real_population(self, capsys: pytest.CaptureFixture[str]) -> None:
        """VACUITY CONTROL. If the glob or the parser returned nothing, every
        assertion above would pass over an empty corpus."""
        import doc_check

        doc_check.main()
        out = capsys.readouterr().out
        total = int(out.split("design documents:")[1].split()[0])
        checked = int(out.split("checked ")[1].split(",")[0])

        assert total >= 50, f"only found {total} design documents"
        assert checked >= 10, f"only {checked} were measurable — the parser may be broken"
