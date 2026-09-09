"""An OPEN acceptance check must be found wherever the document writes it.

MEASURED 2026-09-08. D09.2 said its live check was OPEN in two places — once in
its header, `> The live firing is OPEN — no conversation has rolled over since.`,
and once inside its Verification fence, `# live (OPEN — no rollover since the
restart)`. Neither starts a line and neither is bolded, and `_OPEN_CHECK` required
one or the other. So the report that exists to find exactly this could not see it.

IT WAS OPEN FOR NINE DAYS WITH ITS EVIDENCE ALREADY IN THE LOGS. The line the
document itself greps for — `rollover_summary.correction: standing instruction
recorded` — had fired **17 times**: 4 on 2026-08-31, 11 on 09-01, one on 09-02 and
one on 09-08. Every hit AFTER the document was written, so not one is
satisfied-by-history, and the emitter is reached only when a rollover produced a
correction and an owl was resolved — each hit is the feature working end to end.

THE ORIGINAL ANCHORING WAS A DELIBERATE GUARD, and its own comment said so: bold
or line-start "so a breaker described as OPEN in prose is not swept in". That
reasoning is sound and is kept. What changed is the discriminator: `OPEN` followed
by a DASH is a VERDICT AND ITS REASON. Prose about a breaker reads "the breaker is
open" or "opened"; it does not read "OPEN — because …".

AND THE LOOSENING WAS MEASURED BEFORE IT WAS BELIEVED, exactly as that decision
demanded. Against the whole corpus the widening adds TWO lines, both in D09.2,
both genuine, and no others. Zero false positives is what earns it.

THIS IS THE SAME SHAPE AS DEBT-234, one detector over: an instrument anchored to
the form it first saw, blind to a document that writes the same fact differently.
There the cost was a false "undated" on twenty documents; here it was an open
question nobody could re-ask.

AND IT HAPPENED AGAIN TO THIS VERY FIX — DEBT-245, 2026-09-08. The paragraph above
says the widening "was MEASURED before it was believed" and cites the measurement:
"the widening adds TWO lines, both in D09.2, both genuine, and no others." That
sentence measures PRECISION. It asks whether the new pattern INVENTS markers. It
never asks what marker forms the corpus already CONTAINS that the pattern still
cannot see — RECALL was never measured, and the file you are reading had a test for
each direction of precision and none for recall at all.

MEASURED over every `\bOPEN\b` in the design set: a verdict inside a TABLE CELL is
written `**OPEN.**`, `**OPEN**,` or `**OPEN,` — it starts no line and no dash follows
it, so neither alternative could match. FOUR genuine markers were invisible:
D05.4:420 and D05.4:539 (the basis across a rebuild), D05.8:506 (the envelope
witness) and D09.5:226. The report said 2 open checks in 1 document; the answer was
6 in 3. So the correction was anchored to the form IT first saw, one iteration after
the defect it was fixing.

BOLD is the discriminator, not punctuation — and the one false positive the widening
would have produced is the sharpest part of it. D13.1:195 reads "This check read
`**OPEN**` for a day after it closed", which is the corpus RECORDING THIS EXACT
DEFECT. Blanking code spans before the search is what keeps the instrument from
crying wolf on the document that wrote down the lesson.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_DESIGNS = _ROOT / "docs" / "reference-mapping" / "designs"


def _doc_check():
    path = _ROOT / "scripts" / "doc_check.py"
    spec = importlib.util.spec_from_file_location("_doc_check_open", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_doc_check_open"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.tripwire
def test_an_OPEN_verdict_mid_line_is_found() -> None:
    """D09.2's exact two shapes, kept as fixtures because the live ones are CLOSED.

    Pinning the document itself would have made this test die the moment the
    thing it guards was fixed — which is precisely when the guard starts
    mattering, because the next document to write it that way has nobody
    watching.
    """
    mod = _doc_check()
    for line in (
        "> The live firing is OPEN — no conversation has rolled over since.",
        "# live (OPEN — no rollover since the restart)",
        "the cap is OPEN - nothing has presented one yet",
    ):
        assert mod._OPEN_CHECK.search(line), (  # noqa: SLF001
            f"an OPEN verdict written mid-line is invisible again: {line!r}"
        )


@pytest.mark.tripwire
def test_prose_that_merely_says_open_is_still_not_a_verdict() -> None:
    """THE GUARD THE ORIGINAL DECISION BUILT, and it is kept intact.

    A widening that swept in every sentence containing "open" would cry wolf on
    correct work — the failure this repo pays for most — and the OPEN report
    would become another list nobody reads.
    """
    mod = _doc_check()
    for line in (
        "the tool breaker is open and will close after the cooldown",
        "three breakers opened after the fix shipped",
        "an open question for the operator, queued as ESC-160",
        "we open the database read-only for this query",
    ):
        assert not mod._OPEN_CHECK.search(line), (  # noqa: SLF001
            f"prose about something being open was swept in as a verdict: {line!r}"
        )


@pytest.mark.tripwire
def test_the_detector_still_finds_a_real_population() -> None:
    """VACUITY CONTROL. Both assertions above pass against a regex that matches
    nothing in the actual corpus, so the corpus is asked too."""
    mod = _doc_check()
    found = sum(
        len(mod._open_acceptance_lines(doc.read_text(encoding="utf-8")))  # noqa: SLF001
        for doc in _DESIGNS.glob("*.md")
    )
    assert found >= 2, (
        f"the walk found {found} OPEN acceptance lines across the corpus. Either "
        f"every open check was genuinely closed — check that before believing it — "
        f"or the detector no longer recognises the shape."
    )


@pytest.mark.tripwire
def test_a_verdict_inside_a_table_cell_is_found() -> None:
    """THE FOUR SHAPES THAT WERE INVISIBLE, kept as fixtures.

    Every acceptance table in this corpus puts the verdict in the LAST cell, where
    it starts no line, and writes it with a full stop or a comma rather than a dash.
    Both prior alternatives needed a line start or a following dash.
    """
    mod = _doc_check()
    for line in (
        "| `I2` — the basis holds across a rebuild | all 15 read equal | **OPEN.** Every one",
        "| **I2** — the basis held across a rebuild | 5 rows equal | **OPEN**, exactly as",
        "| **I2** — the envelope path leaves a witness | 0 over 43 | still **OPEN**, but no",
        "* 3 (the seam carries the prompt to the turn) — **OPEN, and it cannot be closed",
    ):
        assert mod._open_acceptance_lines(line), (  # noqa: SLF001
            f"a verdict written in a table cell is invisible again: {line!r}"
        )


@pytest.mark.tripwire
def test_a_quoted_marker_is_prose_ABOUT_a_marker() -> None:
    """The widening's ONLY false positive, and it is the corpus's own account.

    D13.1 records "This check read `**OPEN**` for a day after it closed" — the
    document writing down this very defect. A detector that flagged it would be
    crying wolf on the lesson, which is the failure this repo pays for most.
    """
    mod = _doc_check()
    for line in (
        "**This check read `**OPEN**` for a day after it closed.** The marker was",
        "the report prints `OPEN — no opportunity` when the item is still running",
    ):
        assert not mod._open_acceptance_lines(line), (  # noqa: SLF001
            f"prose quoting a marker was read as carrying one: {line!r}"
        )


@pytest.mark.tripwire
def test_blanking_a_code_span_keeps_the_column_true() -> None:
    """The blanking is length-preserving, so a reported column still points at the
    character it names. A `sub("")` would silently shift every later column."""
    mod = _doc_check()
    line = "a `quoted **OPEN** thing` and then text"
    blanked = mod._CODE_SPAN.sub(lambda m: " " * len(m.group()), line)  # noqa: SLF001
    assert len(blanked) == len(line)
    assert "OPEN" not in blanked


@pytest.mark.tripwire
def test_every_bold_marker_the_corpus_writes_is_seen() -> None:
    """RECALL, asserted for the first time — the direction this file never had.

    A floor on the COUNT was rejected: closing a real open check would then fail
    this test, and a guard that fails on correct work gets bypassed rather than
    satisfied. This asks the invariant instead — whatever the corpus writes in bold
    outside a code span, the detector must return — so a future NARROWING is caught
    while a genuine closure is not.
    """
    mod = _doc_check()
    missed: list[str] = []
    considered = 0
    for doc in _DESIGNS.glob("*.md"):
        for i, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
            bare = mod._CODE_SPAN.sub(lambda m: " " * len(m.group()), line)  # noqa: SLF001
            if "**OPEN" not in bare:
                continue
            considered += 1
            if not mod._open_acceptance_lines(line):
                missed.append(f"{doc.name}:{i}  {line.strip()[:90]}")
    assert considered >= 6, (
        f"only {considered} bold OPEN marker(s) in the corpus — this control exists "
        f"because the assertion below passes vacuously against an empty walk."
    )
    assert not missed, "bold OPEN markers the detector cannot see:\n  " + "\n  ".join(missed)
