"""The OPEN-check detector could not see the shape the corpus actually writes.

MEASURED 2026-09-09. `doc_check.py`'s `OPEN BUT NOT TRACKED` report exists to find
acceptance checks marked OPEN in a document whose item says `validate: done` — checks
nothing will ever re-ask, because `validate_check.py` only re-runs a `closing_check` on
a `partial` stage. It reported **6**. The answer was **10**, and one of the four it could
not see had been CLOSEABLE for eleven days: D09.5's check 3 was satisfied by three live
records from 2026-08-29 that nobody looked at.

WHY, AND IT IS NOT THE FAILURE IT LOOKS LIKE. The comment above `_OPEN_CHECK` records a
correction made on 2026-09-08 that widened recall 2 -> 6 and says it "MEASURED over every
`\\bOPEN\\b` in the design set". It did sweep — so this is not CLAUDE.md's "a list someone
REMEMBERED rather than a set someone SWEPT" a second time. It swept, and then LABELLED
what it found with a discriminator it had already chosen: *"BOLD is the discriminator, not
punctuation."* Anything unbolded read as prose, so eight genuine markers were filed as
prose and the sweep confirmed the rule it started from.

**A DISCRIMINATOR PICKED BEFORE THE GROUND TRUTH IS LABELLED WILL LABEL THE GROUND TRUTH.**
VERIFIED against `268dc678`, the commit that made that correction: all eight were in the
tree that day. `* 5 (live) — OPEN.` sat NINE LINES from `* 3 … — **OPEN,` in the same
bullet list of the same file, and only the bolded one was counted.

So the ground truth now lives HERE, written down independently of the regex: every
`\\bOPEN\\b` line in the design set, judged marker or prose. The regex is measured against
this table rather than against itself, and a line the table does not know about is a
FAILURE — that is the property the 09-08 correction lacked.

WHAT SEPARATES THE TWO, read off the corpus rather than decided in advance. A VERDICT ends
a clause: `**OPEN.**`, `— OPEN.`, `is OPEN**,`, `check 5 OPEN.`, `| **OPEN** |`,
`# 4. Live (OPEN):`, or the end of a heading. PROSE uses the word mid-phrase — "read OPEN
for nine days", "annotates every OPEN site", "meets OPEN first", "went on calling it OPEN
in three places". Three exclusions carry the rest and none is a word list: an article
before it makes it a NOUN ("an OPEN escalation", "not an OPEN"); `.)` after it closes a
PARENTHETICAL aside about a past state ("when it was still OPEN.)"); and a bold span ending
`OPEN**:` introduces a LABEL, which is how D04.1 names this report's sibling category.

THE CORPUS ALSO USES "OPEN" FOR TWO OTHER THINGS, which is why precision is not 100% and
why that is stated rather than tuned away: a circuit BREAKER state ("short-circuit (breaker
OPEN)", "72 short-circuit (breaker OPEN)"), and a file DESCRIPTOR ("KEEPS AN OPEN
DESCRIPTOR ON THE ROTATED ONE"). The one false positive below is a breaker sentence whose
grammar is identical to a verdict — `#   OPEN, not the bounce` against
`which is OPEN, not PASS.` — and no regex can separate those two. Recorded as an exception
with its reason, because a detector tuned until it has no exceptions is a detector tuned to
its sample.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_DESIGNS = _ROOT / "docs" / "reference-mapping" / "designs"

sys.path.insert(0, str(_ROOT / "scripts"))
from doc_check import _CODE_SPAN, _OPEN_CHECK  # noqa: E402

#: Any use of the word at all — the denominator, and deliberately not the detector.
_ANY_OPEN = re.compile(r"\bOPEN\b")

#: THE GROUND TRUTH: (document, a snippet of the line, judgement). Snippets are taken
#: with code spans blanked, exactly as the report reads them, so a marker written inside
#: backticks stays out of both. Judged by hand on 2026-09-09 across the whole design set.
_JUDGED: tuple[tuple[str, str, str], ...] = (
    ('D03.2.md', 'uld have read OPEN forever.]', 'PROSE'),
    ('D03.2.md', '#    OPEN DESCRIPTOR ON THE R', 'PROSE'),
    ('D04.1.md', 'on calling it OPEN in three places for', 'PROSE'),
    ('D04.1.md', 'caught.** An OPEN escalation carries', 'PROSE'),
    ('D04.1.md', 'STILL CALLED OPEN**: a site describin', 'PROSE'),
    ('D04.5.md', 'k CLOSED, one OPEN', 'MARKER'),
    ('D04.5.md', '**OPEN — and the reason fi', 'MARKER'),
    ('D04.5.md', 'not an OPEN**, and the two are', 'PROSE'),
    ('D04.5.md', 'cuit (breaker OPEN)`) and none of them', 'PROSE'),
    ('D04.5.md', '#   OPEN, not the bounce, an', 'PROSE'),
    ('D04.5.md', 'Recorded OPEN deliberately, and i', 'MARKER'),
    ('D04.6.md', 'cuit (breaker OPEN), 60 half-open prob', 'PROSE'),
    ('D05.3.md', '— | Must fail OPEN (present the tool),', 'PROSE'),
    ('D05.4.md', '| **OPEN.** Every one of the', 'MARKER'),
    ('D05.4.md', 'yet, which is OPEN, not PASS.', 'MARKER'),
    ('D05.4.md', '3402 x4) | **OPEN**, exactly as this', 'MARKER'),
    ('D05.8.md', 'ince boot | **OPEN — no opportunity**,', 'MARKER'),
    ('D05.8.md', 'velopes** | **OPEN — no opportunity**', 'MARKER'),
    ('D05.8.md', '3** | still **OPEN**, but no longer 0-', 'MARKER'),
    ('D05.8.md', 'n it is still OPEN is now', 'PROSE'),
    ('D06.3.md', 'ery for I8 is OPEN**, and honestly so', 'MARKER'),
    ('D09.1.md', 'CORE KEEPS AN OPEN DESCRIPTOR ON THE R', 'PROSE'),
    ('D09.2.md', 'ED.** It read OPEN for nine days while', 'PROSE'),
    ('D09.4.md', 'd: step 3 was OPEN and is now CLOSED a', 'PROSE'),
    ('D09.5.md', 'gs; **check 5 OPEN**', 'MARKER'),
    ('D09.5.md', '5 is still **OPEN** with a corrected', 'MARKER'),
    ('D09.5.md', 'PASS, check 5 OPEN.)', 'PROSE'),
    ('D09.5.md', '5 (live) — **OPEN.** Needs one real', 'MARKER'),
    ('D10.3.md', 'was still OPEN.)', 'PROSE'),
    ('D10.5.md', 'uns — **STILL OPEN, and until 2026-09-09', 'MARKER'),
    ('D10.6.md', '# 4. Live (OPEN): the lean path fir', 'MARKER'),
    ('D10.7.md', '**OPEN — nothing is pinned', 'MARKER'),
    ('D11.3.md', '**OPEN — no bookend has be', 'MARKER'),
    ('D13.1.md', 'status meets OPEN first.', 'PROSE'),
    ('D13.1.md', 'notates every OPEN site with whether', 'PROSE'),
    ('D14.4.md', 'alert half is OPEN, and honestly so.**', 'MARKER'),
    ('D14.4.md', 'uld have read OPEN forever.', 'PROSE'),
    ('D16.5.md', '**OPEN — no MCP server has', 'MARKER'),
)

#: The one line the rule cannot get right, kept as an exception rather than tuned away.
#: D04.5's Verification comment says "the OPEN, not the bounce" about a circuit BREAKER
#: event; D05.4's says "which is OPEN, not PASS" about a check verdict. Same grammar,
#: different subject. Flagging one extra line in an annotating report costs a reader one
#: glance; narrowing the rule until this disappears would cost real markers.
_KNOWN_FALSE_POSITIVE = ("D04.5.md", "#   OPEN, not the bounce")


def _open_lines() -> list[tuple[str, int, str]]:
    """Every line in the design set that uses the word, code spans blanked PER LINE.

    Per line, not per file: `_CODE_SPAN` matches across newlines, so blanking a whole
    document at once JOINS lines through an unterminated backtick and shifts every
    number after it. Measured while writing this — a whole-file blank found 30 lines
    where the report finds 38, and reported them at the wrong line numbers.
    """
    out: list[tuple[str, int, str]] = []
    for path in sorted(_DESIGNS.glob("*.md")):
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            blanked = _CODE_SPAN.sub(lambda m: " " * len(m.group()), line)
            if _ANY_OPEN.search(blanked):
                out.append((path.name, i, blanked))
    return out


def _judgement_of(doc: str, line: str) -> str | None:
    for jdoc, snippet, kind in _JUDGED:
        if jdoc == doc and snippet in line:
            return kind
    return None


class TestTheDetectorAgreesWithTheGroundTruth:
    @pytest.mark.tripwire
    def test_every_marker_the_corpus_writes_is_seen(self) -> None:
        """RECALL, which is the half the 09-08 correction never measured. Each miss here
        is an acceptance check that no mechanism will ever re-ask."""
        missed = [
            f"{doc}:{i}  {line.strip()[:90]}"
            for doc, i, line in _open_lines()
            if _judgement_of(doc, line) == "MARKER" and not _OPEN_CHECK.search(line)
        ]
        assert not missed, "status markers the detector cannot see:\n  " + "\n  ".join(missed)

    @pytest.mark.tripwire
    def test_prose_about_a_marker_is_not_reported_as_one(self) -> None:
        """PRECISION, with its one exception named. A report that cries wolf gets
        skipped, which is the failure mode this programme pays for most."""
        wrong = [
            f"{doc}:{i}  {line.strip()[:90]}"
            for doc, i, line in _open_lines()
            if _judgement_of(doc, line) == "PROSE"
            and _OPEN_CHECK.search(line)
            and not (doc == _KNOWN_FALSE_POSITIVE[0] and _KNOWN_FALSE_POSITIVE[1] in line)
        ]
        assert not wrong, "prose flagged as a status marker:\n  " + "\n  ".join(wrong)

    def test_the_known_false_positive_is_still_real(self) -> None:
        """The mirror. If the corpus edits that line away, the exception is dead weight
        and must go — an exemption nobody re-reads is where the next defect hides."""
        hits = [
            (doc, i) for doc, i, line in _open_lines()
            if doc == _KNOWN_FALSE_POSITIVE[0] and _KNOWN_FALSE_POSITIVE[1] in line
        ]
        assert hits, (
            f"{_KNOWN_FALSE_POSITIVE} is no longer in the corpus — delete the exception"
        )

    def test_the_table_knows_every_line_in_the_corpus(self) -> None:
        """THE PROPERTY THE 09-08 CORRECTION LACKED, and the reason this file exists at
        all rather than a wider regex.

        A new OPEN line that nobody judged is exactly how eight markers became prose: the
        sweep was real and the labelling was assumed. Judge the line, add it here with
        its verdict, and the regex is then measured against it.

        NOT a tripwire, deliberately: it fires whenever a design document gains or edits
        an OPEN line, which is ordinary work in this programme, and a gate that fails on
        ordinary work is a gate that gets bypassed rather than satisfied — `doc_check`
        itself is not a gate for the same reason. The full suite and `tests/audit` catch
        it, which is prompt enough for a judgement call.
        """
        unjudged = [
            f"{doc}:{i}  {line.strip()[:90]}"
            for doc, i, line in _open_lines()
            if _judgement_of(doc, line) is None
        ]
        assert not unjudged, (
            "these OPEN lines are in no judgement table — decide MARKER or PROSE for each "
            "and add it to _JUDGED:\n  " + "\n  ".join(unjudged)
        )

    def test_every_table_entry_still_matches_exactly_one_line(self) -> None:
        """The other direction: a stale table entry silently weakens both assertions
        above, because a snippet that matches nothing can never disagree with anything."""
        lines = _open_lines()
        stale = []
        for doc, snippet, _kind in _JUDGED:
            n = sum(1 for d, _i, line in lines if d == doc and snippet in line)
            if n != 1:
                stale.append(f"{doc} :: {snippet!r} matches {n} lines, expected 1")
        assert not stale, "\n  ".join(stale)

    def test_the_population_is_not_empty(self) -> None:
        """VACUITY CONTROL. Every assertion above passes trivially over an empty corpus,
        and a glob that stops resolving is not a hypothetical — this file reads a
        directory by name."""
        lines = _open_lines()
        assert len(lines) >= 30, len(lines)
        kinds = [_judgement_of(d, ln) for d, _i, ln in lines]
        assert kinds.count("MARKER") >= 15 and kinds.count("PROSE") >= 15, (
            f"markers={kinds.count('MARKER')} prose={kinds.count('PROSE')} — a table that "
            "is all one kind cannot measure a discriminator"
        )
