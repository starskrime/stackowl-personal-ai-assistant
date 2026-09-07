"""The staleness check must read every source a document declares — or say it cannot.

WHY THIS EXISTS. `doc_check.py`'s parser has now been too narrow THREE times, and each
time it was widened reactively, after someone noticed. This file is the control that was
missing, because a fourth is otherwise a matter of time.

  1. It missed `Source (new):`-style field names, so documents that HAD followed the
     convention were filed as having no header at all.
  2. It tried cited paths only at the repo root, so the many documents citing
     `tools/registry.py` — relative to the package — resolved to nothing.
  3. MEASURED 2026-09-06, and this one is different in kind: `_header` read ONE LINE per
     field while 47 of 84 documents wrap `Source:` onto following `>` lines. A
     continuation matched neither branch of the loop — it is not a `**Key:**` line, and
     it starts with `>`, so it did not end the header either. It was skipped in silence.

The first two produced UNMEASURABLE, which is visible and honest. The third produced
FRESH. 27 documents lost real, resolvable source paths (66 paths in total), and THREE
then reported fresh while a source they themselves declare had changed after their
verification date — D05.8 was verified 2026-08-30, dated by a 2026-08-29 change, and the
truth was 2026-09-06. A week of drift, reported as freshness.

THE CONTROL IS DIFFERENTIAL, and deliberately so. Asserting "the parser returns what the
parser returns" is the vacuity that let a mutant survive elsewhere in this programme —
a test that RE-DERIVES the code under test proves nothing. So this reads the source
region a second way, naively and independently: take the raw `**Source:**` line and its
continuation lines straight out of the file, pull every backticked citation that resolves
to a real path, and require the parsed field to contain them all. Any narrowing of the
parser — a field spelling, a path shape, a line-wrapping rule — shows up as a document
whose declared source the instrument cannot see.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]

sys.path.insert(0, str(_ROOT / "scripts"))
import doc_check as dc  # noqa: E402

#: A `**Source...:**` field line, any spelling: `Source`, `Source (new)`, `Source (changed)`.
_SOURCE_LINE = re.compile(r">\s*\*\*Source[^:]*:\*\*")
#: Any `**Key:**` field line — what ENDS a run of continuations.
_ANY_FIELD = re.compile(r">\s*\*\*[A-Za-z ()]+:\*\*")


def _raw_source_lines(text: str) -> list[str]:
    """The Source field's lines and its continuations, read straight from the file.

    Independent of `doc_check._header` on purpose — this is the second opinion, and a
    second opinion that calls the first is not one.
    """
    out: list[str] = []
    grabbing = False
    for line in text.splitlines():
        stripped = line.strip()
        if _SOURCE_LINE.match(stripped):
            grabbing = True
            out.append(stripped)
        elif grabbing:
            if stripped.startswith(">") and not _ANY_FIELD.match(stripped):
                out.append(stripped)
            else:
                grabbing = False
    return out


def _raw_source_region(text: str) -> str:
    return " ".join(_raw_source_lines(text))


def _resolvable(blob: str) -> set[str]:
    cited = (p for p in re.findall(dc._CITATION, blob) if "/" in p)
    return {r for p in cited if (r := dc._resolve(p))}


def _docs() -> list[Path]:
    return sorted(dc._DESIGNS.glob("*.md"))


class TestNoDeclaredSourceIsInvisibleToTheInstrument:
    @pytest.mark.tripwire
    def test_the_parser_sees_every_resolvable_path_the_document_declares(self) -> None:
        """THE CONTROL. A path a document declares, which exists on disk, and which the
        staleness check never looks at, is how a stale document reports fresh."""
        lost: dict[str, list[str]] = {}
        for doc in _docs():
            text = doc.read_text(encoding="utf-8")
            parsed = _resolvable(dc._source_fields(dc._header(text)))
            declared = _resolvable(_raw_source_region(text))
            missing = sorted(declared - parsed)
            if missing:
                lost[doc.name] = missing

        assert not lost, (
            "these documents declare sources the staleness check cannot see, so each "
            "can report FRESH while the code underneath it moves:\n"
            + "\n".join(f"  {k}: {v}" for k, v in lost.items())
        )

    def test_the_control_sees_a_real_corpus(self) -> None:
        """VACUITY CONTROL. If the naive re-read found nothing, the assertion above
        would pass over an empty set — which is the exact failure it guards."""
        declared = sum(len(_resolvable(_raw_source_region(d.read_text("utf-8"))))
                       for d in _docs())

        assert len(_docs()) >= 60, len(_docs())
        assert declared >= 100, f"only {declared} resolvable declared paths found"


class TestTheShapesTheCorpusActuallyUses:
    """Each of these is a shape the parser did NOT handle at some point. They are pinned
    as behaviour rather than as a list of filenames, because the documents get
    re-verified and a filename pin would rot into a false alarm."""

    def test_a_wrapped_field_is_joined_not_truncated(self) -> None:
        head = dc._header(
            "# T\n\n> **Source:** `src/stackowl/ipc/` (frames),\n"
            "> `src/stackowl/channels/socket_adapter.py`\n> **Config:** none\n"
        )

        assert "socket_adapter.py" in head["Source"], head
        assert head["Config"] == "none", "the continuation swallowed the next field"

    def test_a_field_after_a_wrapped_one_is_still_its_own_field(self) -> None:
        head = dc._header(
            "> **Source:** `a/b.py`,\n> `c/d.py`\n> **Last verified:** 2026-09-06\n"
        )

        assert head["Last verified"] == "2026-09-06", head

    def test_a_symbol_citation_resolves_to_its_file(self) -> None:
        """`module.py::Symbol` names a symbol inside a file. The FILE is what git can
        date, and D09.6 named its only source that way."""
        assert dc._resolve("src/stackowl/db/pool.py::default_db_path") is not None

    def test_a_symbol_citation_is_matched_at_all(self) -> None:
        """Excluding `:` did not make such a citation resolve to nothing — it made the
        citation invisible, so the document read as declaring NO sources."""
        found = re.findall(dc._CITATION, "see `src/stackowl/db/pool.py::default_db_path`")

        assert found == ["src/stackowl/db/pool.py::default_db_path"], found

    def test_the_header_still_ends_at_the_prose(self) -> None:
        """Joining continuations must not run the header into the body."""
        head = dc._header("> **Source:** `a/b.py`\n\n## Why this exists\n\n> a quote\n")

        assert set(head) == {"Source"}, head


class TestTheCorpusReallyContainsTheseShapes:
    """If the corpus stopped using a shape, the tests above would pass on fixtures while
    guarding nothing real. These say the shapes are still out there."""

    def test_documents_still_wrap_their_source_field(self) -> None:
        """COUNTS LINES, and the first version of this test did not — it compared word
        counts between the raw region and the parsed field, which are now EQUAL by
        construction because the fix joins them. It passed on every document with a
        Source field and would have passed on a corpus that stopped wrapping entirely:
        a vacuity control that was itself vacuous."""
        wrapped = [d.name for d in _docs() if len(_raw_source_lines(d.read_text("utf-8"))) > 1]

        assert len(wrapped) >= 20, (
            f"only {len(wrapped)} documents wrap their Source field; the measurement on "
            "2026-09-06 found 47, and if the corpus really stopped wrapping then the "
            "continuation tests above guard a shape nothing uses"
        )

    def test_the_measurable_population_has_not_collapsed(self) -> None:
        """A parser that silently narrows again shows up here first: measurable was 47
        of 84 on 2026-09-06, up from 45 before the continuation fix."""
        measurable = 0
        for doc in _docs():
            head = dc._header(doc.read_text(encoding="utf-8"))
            if not re.search(r"\d{4}-\d{2}-\d{2}", head.get("Last verified", "")):
                continue
            if _resolvable(dc._source_fields(head)):
                measurable += 1

        assert measurable >= 45, f"only {measurable} documents are datable"
