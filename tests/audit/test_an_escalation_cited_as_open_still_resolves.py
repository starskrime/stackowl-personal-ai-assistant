"""An escalation cited as open must still RESOLVE in the queue.

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

AND THE REPORT NAMES A FACT, NOT A VERDICT — its first version did not, and this file
was renamed when that was fixed. It was called ANSWERED BUT STILL CALLED OPEN, which
asserts the citation is the stale half. MEASURED 2026-09-07, one loop later: true of only
three of five sites. Two described ESC-20's terse-compression half, which is GENUINELY
OPEN — "STILL OPEN: whether a scheduled briefing should be compressed at all", pinned by
`test_terse_does_NOT_compress_a_scheduled_briefing` — and had simply FALLEN OUT of the
queue when `480571bc` restructured ESCALATIONS from a pruned LIST to a retained DICT. A
half-answered entry had no home under the old convention: the moment any part resolved,
the whole entry looked resolved. A reader trusting the old name would have deleted the
last live record of an unanswered product question and called it tidying — the empty
table this codebase already names, one level up. Absence is a QUESTION, not an answer.

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
  * THE DETECTOR'S OWN EXPLANATION OF ITS FINDINGS. Rewriting the report to say what it
    means required writing "ESC-20 is genuinely open" into `escalation_check.py`, and it
    reported itself on the next run. A detector that reads prose will eventually read its
    own; that file and `tests/audit/` are skipped for the same reason — both hold
    explanatory text ABOUT findings rather than findings.

THE POPULATION IS ZERO TODAY, which is exactly when a blind instrument is invisible: an
empty report and a broken walk print the same thing. `test_the_walk_actually_reads_the_tree`
below is the control.

63 dangling ids are cited across the tree and most are CORRECT: an answer recorded where
it changed something is exactly right. Only a present-tense open claim is a finding.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "scripts"))

from escalation_check import (  # noqa: E402
    _OPEN_NOW,
    _PAST_TENSE,
    _blank_quotes,
    _still_called_open,
)


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


@pytest.mark.tripwire
def test_the_three_false_positives_stay_dead_including_the_detectors_own_prose() -> None:
    """The fourth shape: the report explaining itself. Guarded by a skip, not a regex —
    so what is asserted is that the skip is still in force, on the live file."""
    from escalation_check import _SKIP

    assert "/scripts/escalation_check.py" in _SKIP, (
        "the detector will report its own explanation of the findings"
    )
    assert "/tests/audit/" in _SKIP, "the audit fixtures quote the corpus verbatim"


@pytest.mark.tripwire
def test_the_walk_actually_reads_the_tree() -> None:
    """THE CONTROL, and the reason it exists: the report is EMPTY today.

    An empty report and a walk that reads nothing print the same thing. Passing a live
    set of NOTHING makes every present-tense open citation in the tree dangle, so a
    working walk must return sites — and each must carry a real path, a positive line
    number and at least one id. No count is pinned: this asserts the CAPABILITY, never
    the population, which is the rule two of this loop's own guards were fixed to obey.
    """
    found = _still_called_open(live=set())

    assert found, "the walk read no files at all; the report would be silently empty"
    for rel, line_no, ids, text in found:
        assert not rel.startswith("/"), f"path should be repo-relative: {rel}"
        assert line_no > 0 and ids and text.strip()


@pytest.mark.tripwire
def test_the_walk_prunes_the_caches_and_keeps_the_instruction_surfaces() -> None:
    """The prune list is a decision about what the report can SEE, so pin both halves.

    MEASURED 2026-09-07. The first version filtered paths after `rglob("*")` had already
    traversed them — 122,273 paths, 48,849 candidate files, **48.3 seconds on every run
    of `escalation_check.py`**, essentially all of it `.venv` (5.7 GB, 18,988 candidates)
    and `.uv-cache` (5.8 GB, 21,960). Pruning those two by name: 2.3s, identical results.

    The second version over-corrected and dropped every DOT-directory, which silently
    took `.claude/skills/` — the instruction surface this loop reads on every invocation
    — out of scope to save nothing. Both halves are asserted because only asserting the
    fast half is how a surface goes dark without anyone choosing it.
    """
    from escalation_check import _PRUNE

    for cache in (".venv", ".uv-cache", ".git", "__pycache__"):
        assert cache in _PRUNE, f"{cache} is what made the walk cost 48s"
    for live_surface in (".claude", ".agents", ".github", "docs", "src", "tests"):
        assert live_surface not in _PRUNE, (
            f"{live_surface} carries live instruction; pruning it hides findings"
        )
