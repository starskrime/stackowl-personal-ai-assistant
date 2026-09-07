"""An acceptance check a document marks OPEN must stay ENUMERATED.

WHY THIS EXISTS — the fifth time this programme has cured one disease, and the first
time the population it could not see was on the ITEM side rather than the record side.

`validate_check.py` re-runs a closing check only for an item whose `validate` stage is
`partial`. So a design document that says **OPEN** while its item says `validate: done`
holds a claim NOTHING will ever re-ask: the same dead end `premise_check`,
`closing_check` and `doc_check.py` were each built to cure, one population over.

MEASURED 2026-09-07: seven documents carry an OPEN acceptance line and FOUR sat on items
marked done — D04.1, D04.5, D05.8 and D13.1, five checks between them. D04.5 is the one
that shows the cost. Its open check read *"it needs a tool breaker to open, and there
have been zero such events in five days."* Three breakers had opened since the fix
shipped, one on the shipping day itself; the trigger is not the open but a re-dispatch
AFTER it (52 opens produced 12 bounces); and the query named `stackowl.jsonl`, one file,
so it could not see past the next rotation. Three wrong things in one check, sitting in
a document whose own body five sections up cited the contradicting number — and no run
could have surfaced any of it, because nothing re-ran it.

WHAT IS GUARDED IS THE INSTRUMENT, never the population's size. The backlog is not a
gate, for the reason `doc_check.py` already records: "a tripwire would fail every
unrelated change until someone re-read fifteen documents, which is how a gate gets
bypassed rather than satisfied." A detector that silently stops matching, or a stage
lookup that silently returns nothing, reproduces the original failure exactly — and
`doc_check.py` has already been blind once, under-reporting staleness by eleven.

The raw matching lines were printed and READ before the count of seven was believed. The
first print landed on `m.start()` and showed an empty line for D04.5 and nothing at all
for D13.1, which would have made a working detector look broken.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "scripts"))

from doc_check import _open_acceptance_lines, _validate_stages  # noqa: E402


@pytest.mark.tripwire
def test_it_finds_the_shapes_the_corpus_actually_writes() -> None:
    """Every OPEN shape live in the corpus today, verbatim from the four documents."""
    corpus = "\n".join(
        [
            "**OPEN — and the reason first recorded here was wrong three ways.** More",
            "| D5-order | Dispatch precedes the callback on both | **OPEN — ESC-25** |",
            "**OPEN**, and the closing query had to be written twice, which is the",
            "  **OPEN — no opportunity**, not success |",
        ]
    )
    found = _open_acceptance_lines(corpus)
    assert len(found) == 4, f"a live shape stopped matching: {found}"


@pytest.mark.tripwire
def test_a_circuit_described_as_open_in_prose_is_not_an_acceptance_check() -> None:
    """The word OPEN is common in this corpus; only the marker shape counts.

    D04.5 itself talks about breakers being open in five different sentences. A detector
    that swept those in would report a backlog made mostly of noise, and a report nobody
    believes is a report nobody drains.
    """
    prose = "\n".join(
        [
            "`[resilient_round] decision — short-circuit (breaker OPEN)` fires when",
            "the circuit is open for the remainder of the turn, so the tool is",
            "OPENING the breaker is not the trigger; a re-dispatch is.",
            "provider breakers opened 151 times over 09-01..09-07",
        ]
    )
    assert _open_acceptance_lines(prose) == []


@pytest.mark.tripwire
def test_the_stage_lookup_can_actually_see_the_items() -> None:
    """The report is silent when the lookup returns nothing — so prove it does not.

    This is the half that fails invisibly: a missing PyYAML, a moved `progress.yml` or a
    renamed `items` key turns "four untracked open checks" into "none", which reads as a
    clean bill of health. Assert the CAPABILITY — that real ids come back with real
    stages — never how many of them are `done`.
    """
    stages = _validate_stages()
    assert stages, "the stage lookup went blind; the report would print nothing"
    assert "D04.5" in stages, f"a known item id is missing: {sorted(stages)[:5]}"
    assert set(stages.values()) & {"done", "partial"}, "no stage value was recognised"


@pytest.mark.tripwire
def test_it_says_whether_the_document_also_records_a_close() -> None:
    """The report must state a FACT about each site, never a verdict about it.

    MEASURED 2026-09-07 over the three live sites, and they split exactly two ways.
    D13.1 marked a check `**OPEN**` and said `**CLOSED 2026-09-06**` TWELVE LINES BELOW
    it — a status marker written before the outcome, the outcome appended underneath, and
    the marker never updated. D05.8's two rows carry no close statement anywhere in the
    document and are genuinely waiting on "0 planned envelopes ran". Same report, opposite
    actions, and nothing in the line itself told them apart.

    So the annotation asks a question with a checkable answer — does this document record
    a close, and where — rather than guessing which case a site is in. That is the lesson
    the escalation report had to learn one loop earlier, when naming a verdict nearly cost
    a live operator question.
    """
    from doc_check import _CLOSE_STATEMENT, _close_note

    assert _CLOSE_STATEMENT.search("**CLOSED 2026-09-06** against `c625d74f`.")
    assert _CLOSE_STATEMENT.search("**RESOLVED** — the operator chose the second option")
    assert not _CLOSE_STATEMENT.search(
        "the breaker closed again after the cooldown elapsed"
    ), "an ordinary sentence about something closing is not a close STATEMENT"
    assert not _CLOSE_STATEMENT.search(
        "| **I1** — the configured cap binds on every path | **OPEN — no opportunity** |"
    ), "an open row must not be read as its own close"

    # BOTH BRANCHES, on a synthetic document — the live corpus can no longer reach the
    # "also says CLOSED" one. After D13.1 was fixed only D05.8 remains and both of its
    # rows are the "no close" case, so a mutation hardcoding the answer changed NOTHING
    # in the report. Measured, on the first attempt to prove it.
    superseded = "line one\n**OPEN**, and the query was written twice\nprose\n**CLOSED 2026-09-06**\n"
    assert _close_note(superseded, 2) == "document also says CLOSED/RESOLVED at line 4"

    waiting = "line one\n| **I1** | 0 envelopes | **OPEN — no opportunity** |\nmore prose\n"
    assert _close_note(waiting, 2) == "no close statement anywhere in this document"

    # A close ABOVE the marker does not supersede it — the marker came later.
    close_first = "**RESOLVED** earlier\n**OPEN — a second, later question**\n"
    assert _close_note(close_first, 2) == "no close statement anywhere in this document"
