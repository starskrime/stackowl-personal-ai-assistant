"""A log-reading check becomes a RECORD once its evidence rotates away.

WHY THIS EXISTS — the eighth variant of the closing-check family, and the first whose
cause is TIME rather than wording.

A Verification command that reads the logs and records "PASS 2026-08-21: 8 occurrences"
is a CHECK for as long as the logs reach back that far, and a RECORD afterwards. Once the
evidence rotates the command returns 0 forever, and 0 reads as failure to whoever runs it
next. The seven variants before this one were all about what a check ASKS; this one is
about how long the answer survives. THE EVIDENCE HAD A SHORTER LIFETIME THAN THE DOCUMENT.

MEASURED 2026-09-07: four such commands across three documents, the oldest citing
2026-07-27 against logs that begin 2026-08-28. D16.3's was the sharpest — its evidence
was a throwaway contributor plugin installed to prove the path and then REMOVED, so the
line it greps cannot fire again even in principle.

THE HORIZON IS READ FROM THE FILES, NEVER FROM THE CONFIG. `backupCount` is 30 and
`getFilesToDelete()` returns nothing, yet only ten dated files exist — the horizon is
young, not over-pruned, because a deletion incident on 2026-08-30 left two and daily
rotation has added one since. A detector keyed on the intended 30 would have reported
nothing while three documents cited evidence already gone.

WHAT IS GUARDED IS THE INSTRUMENT. Two false positives were designed out and are pinned
below, because both would cry wolf on documents that had done the work:

  * a FRESHER re-run recorded in the same block clears the finding — D08.1 carries
    "RAN 2026-08-17 -> 4 firings" with "RE-RAN 2026-09-07 -> 99" on the next line;
  * a command QUOTED IN PROSE is not a Verification command. The document that FIXES this
    defect has to quote the retired command to explain it, and the first, line-scoped
    version of this detector flagged that correction as the defect — the same shape as the
    escalation report reading its own explanation one loop earlier.

AND THE INSTRUMENT WAS ITSELF WRONG TWICE, MEASURED 2026-09-07 — one loop after the
report was drained to zero and reported as drained. It was reading zero because it was
BLIND, and an empty report could not say which.

  * IT ASKED PROSE FOR A DATE THAT WAS ALREADY A STRUCTURED TOKEN. The finder matched
    ``(PASS|MEASURED|RAN|RE-RAN|Measured|Re-ran)`` then whitespace then the date — six
    hand-written words over prose that is unbounded by construction — and saw a date
    beside 24 of the 43 log-query windows that carry one. `test_it_reads_the_date_not_the_word_before_it`
    pins the five phrasings it missed, verbatim from the corpus. This is the cause
    `_test_paths_on_command_lines` rejected one loop earlier for NEGATION, in the mirror:
    the lesson was learned in one detector and not carried to its sibling twenty lines
    away in the same file.
  * THE WINDOW WAS TEN LINES AND BLED INTO THE NEXT COMMAND. With every date counting,
    D01.7 step 8 was flagged on step 9's evidence — a different command, in a different
    fence, whose own fresher re-run sat one line past the window's end. Evidence attaches
    to a BLOCK, not to a LINE, and the two shapes are pinned below in both directions:
    a block must not inherit the NEXT block's dates, and it must still see a reading
    written as prose UNDER its closing fence, which is where D05.8's real one lives.

Every dated block is now RETURNED, rotted or not, so the report can print its
denominator. That is not decoration: the silent zero above is exactly what a clean corpus
looks like, and this detector spent a loop indistinguishable from a working one.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "scripts"))

from doc_check import _evidence_older_than_the_logs, _log_horizon  # noqa: E402

_HORIZON = "2026-08-28"


@pytest.mark.tripwire
def test_it_finds_a_fenced_command_whose_evidence_has_rotated() -> None:
    doc = (
        "## Verification\n\n```bash\n"
        "grep -h 'plugin parts contributed' ~/.stackowl/logs/stackowl*.jsonl | tail\n"
        "# PASS 2026-08-21: 8 occurrences.\n```\n"
    )
    found = _evidence_older_than_the_logs(doc, _HORIZON)
    assert len(found) == 1, found
    assert found[0][1] == "2026-08-21"
    assert found[0][3] is True, found


@pytest.mark.tripwire
def test_a_fresher_re_run_in_the_same_block_clears_it() -> None:
    """The D08.1 shape. Flagging this would cry wolf on a document already corrected."""
    doc = (
        "```bash\n"
        "grep -h 'nudge: due' ~/.stackowl/logs/stackowl*.jsonl | wc -l\n"
        "# PASS: >0. RAN 2026-08-17 -> 4 firings.\n"
        "# RE-RAN 2026-09-07 -> 99 firings over ten retained days.\n```\n"
    )
    found = _evidence_older_than_the_logs(doc, _HORIZON)
    assert [f[3] for f in found] == [False], found


@pytest.mark.tripwire
def test_a_command_quoted_in_prose_is_not_a_verification_command() -> None:
    """The correction shape — and the first version of this detector failed it.

    A document retiring a rotted check must QUOTE the command to explain what it retired.
    That is a description, not an instruction, and only a fence distinguishes them.
    """
    doc = (
        "The command this section used to carry was\n"
        "`grep -h 'plugin parts contributed' ~/.stackowl/logs/stackowl*.jsonl`, with **PASS\n"
        "2026-08-21: 8 occurrences**. Re-run today it returns 0, and it always will.\n"
    )
    assert _evidence_older_than_the_logs(doc, _HORIZON) == []


@pytest.mark.tripwire
def test_an_unknowable_horizon_reports_nothing_rather_than_everything() -> None:
    """Degrade to silence, never to a wrong report: with no logs there is no horizon, and
    every recorded date would otherwise look older than it."""
    doc = "```bash\ngrep x ~/.stackowl/logs/stackowl.jsonl\n# PASS 2020-01-01: 3\n```\n"
    assert _evidence_older_than_the_logs(doc, "") == []


#: The five phrasings the retired keyword whitelist could not see, VERBATIM from the
#: corpus. Every one records a run; not one is spelled the way the whitelist expected.
_REAL_PHRASINGS = [
    "# PASS (observed 2026-07-27): requested -> old incarnation ended -> honoured ->",
    "### Live reading, 2026-08-22",
    "# RAN: 201, across ten retained days (2026-08-01 to 2026-08-11).",
    "All three RUN on 2026-08-04, not trusted.",
    "**RE-RUN 2026-08-11:** 9 passed; 1130 passed / 0 failed.",
]


@pytest.mark.tripwire
@pytest.mark.parametrize("phrasing", _REAL_PHRASINGS)
def test_it_reads_the_date_not_the_word_before_it(phrasing: str) -> None:
    """THE DEFECT THIS REPLACED, pinned five ways.

    The finder used to require one of six hand-written words immediately before the date.
    Across 84 documents it saw 24 of the 43 windows that carry one, so the report printed
    a confident ZERO while real records rotted. A regex over prose is a guess at future
    phrasing — the same conclusion `_test_paths_on_command_lines` reached one loop earlier
    for negation. The date is a STRUCTURED token; read that instead.
    """
    doc = (
        "```bash\n"
        "grep -h 'x' ~/.stackowl/logs/stackowl*.jsonl | wc -l\n"
        f"{phrasing}\n```\n"
    )
    found = _evidence_older_than_the_logs(doc, _HORIZON)
    assert [f[3] for f in found] == [True], (phrasing, found)


@pytest.mark.tripwire
def test_a_block_does_not_inherit_the_next_blocks_evidence() -> None:
    """The D01.7 shape — and the first widening produced exactly this false positive.

    Step 8 carries no date of its own. Step 9, in a DIFFERENT fence, records an old
    result and refutes it on the next line. A ten-line window starting at step 8 reached
    step 9's old date and stopped one line short of the refutation, so the widened rule
    flagged a command whose neighbour had already done the work. Evidence attaches to a
    BLOCK, not to a LINE.
    """
    doc = (
        "```bash\n"
        "# 8 — the sweeper is wired\n"
        "cat ~/.stackowl/logs/stackowl*.jsonl | jq -c 'select(.msg|test(\"registered\"))'\n"
        "# PASS: all four conditions are true.\n"
        "```\n\n```bash\n"
        "# 9 — the full chain\n"
        "cat ~/.stackowl/logs/stackowl*.jsonl | jq -r 'select(.msg|test(\"rollover\")) | .msg'\n"
        "# PASS (observed 2026-07-27): requested -> ended -> honoured.\n"
        "# RE-RUN 2026-09-07: the chain fires, 63 against 63.\n"
        "```\n"
    )
    found = _evidence_older_than_the_logs(doc, _HORIZON)
    assert [f[3] for f in found] == [False], found
    assert found[0][1] == "2026-09-07", found


@pytest.mark.tripwire
def test_a_reading_written_under_the_closing_fence_is_this_blocks_evidence() -> None:
    """The D05.8 shape, and the ONE real finding on the live corpus.

    Its reading is a `### Live reading, 2026-08-22` heading two lines BELOW the fence.
    The obvious structural fix for the test above — score only what is inside the fence —
    is a trap: it silences this, and it silences D08.1, whose one-line fence has its
    result in prose underneath. The window has to leave the block; it just must not enter
    the next one.
    """
    doc = (
        "```bash\n"
        "grep -c 'plan: envelope set' ~/.stackowl/logs/stackowl*.jsonl\n"
        "```\n\n"
        "### Live reading, 2026-08-22\n\n"
        "Bounded to the core incarnation booted at `16:12:35Z`.\n"
    )
    found = _evidence_older_than_the_logs(doc, _HORIZON)
    assert [f[3] for f in found] == [True], found
    assert found[0][1] == "2026-08-22", found


@pytest.mark.tripwire
def test_a_neighbours_introduction_is_not_this_blocks_evidence() -> None:
    """WHY THE TRAILING WINDOW IS BOUNDED, measured rather than chosen.

    A review recommended letting the window run to the next fence, on the reasoning that
    a positional bound needs no number. Swept over the live corpus that gives NINE
    findings where the bounded window gives one, and EIGHT of the nine are false —
    including five hits on a heading that says *"Every command below has been RUN"*, which
    introduces the NEXT block and says so in its own words. The answer is invariant for a
    trailing bound of 2..8 and wrong outside it; eight is the top of that plateau.
    """
    doc = (
        "```bash\n"
        "grep -h 'x' ~/.stackowl/logs/stackowl*.jsonl | wc -l\n"
        "```\n"
        + "\nfiller line that carries no date at all.\n" * 6
        + "\n**Every command below has been RUN, on 2026-07-28 against commit `2f3a43f7`.**\n"
        "\n```bash\ngrep -h 'y' ~/.stackowl/logs/stackowl*.jsonl | wc -l\n```\n"
    )
    assert _evidence_older_than_the_logs(doc, _HORIZON) == [], (
        "the first block borrowed the second block's introduction as its own evidence"
    )


@pytest.mark.tripwire
def test_a_command_split_across_a_backslash_continuation_is_still_seen() -> None:
    """MEASURED 2026-09-07: fifteen fenced blocks in this corpus name a `.jsonl` log and
    were invisible, because the reader sits on one line and the glob on the next. Three
    of the five commands in the block holding the one real finding are that shape.
    `_newest_by_pipe_position` already joined continuations for the same reason; a
    detector that reads a subset of the commands reports a denominator it has not seen.
    """
    doc = (
        "```bash\n"
        "jq -rc 'select(.msg|test(\"envelope\"))|.ts' \\\n"
        "  ~/.stackowl/logs/stackowl*.jsonl | wc -l\n"
        "# PASS 2026-08-21: 8 occurrences.\n```\n"
    )
    found = _evidence_older_than_the_logs(doc, _HORIZON)
    assert [f[3] for f in found] == [True], found


@pytest.mark.tripwire
def test_a_quote_wrapped_jq_program_is_still_a_log_read() -> None:
    """THE CO-OCCURRENCE WHITELIST, which is the prose defect wearing a structural
    disguise. The matcher used to require one of five reader words on the same joined
    command as the path; the regex LOOKS like structure, and it is a word list. Fourteen
    commands in this corpus hide the reader inside a quoted `jq` program or use
    `glob.glob(...)` from Python. A `.jsonl` under `~/.stackowl/logs`, inside a fence, is
    a log read whatever spells the reading — measured at +2 examined blocks and no
    changed verdict, which is what a coverage fix should look like.
    """
    doc = (
        "```bash\n"
        "jq -r 'select(.msg|test(\"x\"))\n"
        "  | \"\\(.ts)\"' ~/.stackowl/logs/stackowl.jsonl\n"
        "# PASS 2026-08-21: 8 occurrences.\n```\n"
    )
    found = _evidence_older_than_the_logs(doc, _HORIZON)
    assert [f[3] for f in found] == [True], found


@pytest.mark.tripwire
def test_every_dated_block_is_returned_so_the_report_can_print_a_denominator() -> None:
    """A clean corpus and a blind finder print the same empty report, and this one spent
    a whole loop indistinguishable from a working detector. Returning the rotted flag
    rather than filtering on it is what lets the caller say `none, across N`."""
    doc = (
        "```bash\ngrep -h 'a' ~/.stackowl/logs/stackowl*.jsonl\n# RAN 2026-09-07 -> 4\n```\n"
        "\n```bash\ngrep -h 'b' ~/.stackowl/logs/stackowl*.jsonl\n# RAN 2026-08-01 -> 9\n```\n"
        "\n```bash\ngrep -h 'c' ~/.stackowl/logs/stackowl*.jsonl\n# no date here at all\n```\n"
    )
    found = _evidence_older_than_the_logs(doc, _HORIZON)
    assert [f[3] for f in found] == [False, True], found


@pytest.mark.tripwire
def test_the_horizon_actually_reads_the_files() -> None:
    """THE CONTROL. An empty horizon silences the whole report, so a broken reader and a
    clean corpus print the same thing. Assert the CAPABILITY — a real ISO date back from
    the log directory — never how many documents it happens to catch."""
    horizon = _log_horizon()
    assert horizon, "no horizon: the report would be silently empty"
    assert len(horizon) == 10 and horizon.count("-") == 2, horizon
