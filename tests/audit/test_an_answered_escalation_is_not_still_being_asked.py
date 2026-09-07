"""An escalation the operator ANSWERED must not still be described as open.

WHY THIS EXISTS — the sixth time this programme has cured one disease, and the first
time on the ANSWER side of the queue.

An OPEN escalation carries a `premise_check`, and `escalation_check.py` re-runs it every
loop. An ANSWERED one is **deleted from the queue** — so nothing re-reads the sites that
cited it, and every one of them keeps asking the operator for a decision he already made.
The gap was already diagnosed and deferred, in the docstring of
`test_a_settled_decision_is_not_contradicted_by_the_record`: "an ANSWERED escalation has
no `decision_check` the way an open one has a `premise_check` — recorded in the item, not
built here."

MEASURED 2026-09-07, seventeen days after the answer. Bakir settled ESC-25 on 2026-08-21
— dispatch first, on both loops, deliberately, his call against my recommendation — and
it was removed from the queue in `3306a340` and pinned by
`test_dispatch_precedes_the_callback_on_both_BY_DECISION`. D04.1 went on calling it OPEN
in three places, and `test_both_tool_loops_conform.py` contradicted ITSELF: line 180 said
"the order question is still open" thirty lines above the docstring recording the answer.
ESC-26 was answered in the same commit and called open in the same document.

WHAT IS GUARDED IS THE INSTRUMENT, never the population's size — the backlog is a report,
drained one item per loop, for the reason `doc_check.py` records about gates that fail
every unrelated change. And the instrument needs guarding badly: it separates a finding
from a correct sentence by ONE TOKEN, and produced three distinct false positives before
it was right.

  * `fail-open` in `durable/loop.py` — the word `open` inside a hyphenated compound.
  * "asserted while ESC-23 **was** open" — a correct historical note, one auxiliary verb
    away from a live claim.
  * A CORRECTION QUOTING THE CLAIM IT RETIRES. The detector flagged the very document
    that fixes the first case it found, because that document quotes *"the order question
    is still open"* in the act of retiring it. And prose WRAPS, so the quotation opened on
    one line and closed on the next — a line-local strip still flagged it.

63 dangling ids are cited across the tree and most are CORRECT: an answer recorded where
it changed something is exactly right. Only a present-tense open claim is a finding.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "scripts"))

from escalation_check import _OPEN_NOW, _PAST_TENSE, _blank_quotes  # noqa: E402


def _flags(line: str) -> bool:
    """The detector's decision for one line, quotes already blanked."""
    clean = _blank_quotes(line)
    return bool(_OPEN_NOW.search(clean)) and not _PAST_TENSE.search(clean)


@pytest.mark.tripwire
def test_it_finds_the_shapes_the_tree_actually_writes() -> None:
    """Every live phrasing, verbatim from the sites the first run reported."""
    for line in [
        "| D5-order | Dispatch precedes the callback on both | **OPEN — ESC-25** |",
        "One extra tool executes. **Open: ESC-25.**",
        "**ESC-26** (the re-scope orphaned ESC-14, **open**)",
        "formatted, so it remains ESC-20's open question rather than arriving as a",
        "the per-turn prefix churn that ESC-12 is open to fix.",
        "ESC-22, open with Bakir. A test below pins that the re-run still happens",
    ]:
        assert _flags(line), f"a live shape stopped matching: {line!r}"


@pytest.mark.tripwire
def test_the_three_false_positives_stay_dead() -> None:
    """Each cost a wrong report before it was found. A detector that cries wolf on
    correct work is the failure this programme keeps paying for."""
    assert not _flags("# decision (ESC-17), not a repair. Same fail-open contract"), (
        "fail-open is a contract, not an open question"
    )
    assert not _flags('The inverse of what this test asserted while ESC-23 was open.'), (
        "a historical note is not a live claim"
    )
    assert not _flags('D04.1 said *"ESC-25 is still open"* and that was wrong.'), (
        "a correction quoting the claim it retires is not making that claim"
    )


@pytest.mark.tripwire
def test_a_quotation_that_wraps_is_still_a_quotation() -> None:
    """Prose wraps. A line-local strip missed this and flagged the corrected document."""
    wrapped = (
        'line 180 said *"the order question\n'
        'is still open"* thirty lines above the answer. **ESC-26** was answered too.\n'
    )
    second = _blank_quotes(wrapped).splitlines()[1]
    assert not (_OPEN_NOW.search(second) and not _PAST_TENSE.search(second)), (
        "the closing half of a wrapped quotation still read as a claim"
    )
    assert len(_blank_quotes(wrapped).splitlines()) == len(wrapped.splitlines()), (
        "blanking changed the line count, so reported line numbers would be wrong"
    )


@pytest.mark.tripwire
def test_blanking_quotes_does_not_swallow_a_real_claim_after_them() -> None:
    """The capability risk of a newline-spanning strip: an unbalanced quote earlier in a
    file could blank everything after it, and the report would go silently empty."""
    text = 'He said "yes" and then: ESC-99 is open with Bakir.\n'
    assert _flags(_blank_quotes(text)), "a claim after a closed quotation was swallowed"
