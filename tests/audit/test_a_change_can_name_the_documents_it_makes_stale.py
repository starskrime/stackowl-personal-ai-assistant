"""A change could not name the documents it was about to make stale.

MEASURED 2026-09-09, on this loop's own previous commit. `1897e0c3` promoted one log
line in `commands/registry.py` from DEBUG to INFO and left TWO design documents stale
— D14.1 and D14.2 — which the next run of `doc_check.py` duly reported. That is the
fifth-surface rule failing in the one place it is hardest to excuse: the author had
just read the rule, in the skill, in the same session.

THE RULE IS NOT THE PROBLEM. CLAUDE.md and the item-loop skill both say the document
is the fifth surface of a change, in the strongest terms the corpus has — `d8b8ba81`
retired the tool-loop hard stops across code, config, tests and a docstring, and D05.7
advertised the deleted flag for eight days. **A warning names the HAZARD; the reader
needs the ANSWER.** That is the exact sentence `tests_touching.py` was built on after
DEBT-228 shipped a red test the warning had already described. The test question has
had a derived answer since then. The DOCUMENT question had only a post-hoc report,
which is to say an answer that arrives on somebody else's loop.

WHAT THAT COSTS, measured over the last 60 commits: **15 of them touched a path some
design document declares as its `Source`** — one in four. `bf603ef7` reached NINE
documents, `c82dac21` seven. Two entire loops (DEBT-242, DEBT-254) were spent draining
staleness that earlier loops created, and DEBT-254's own record says its eight
documents "were made stale by MY OWN commits in this session".

AND THE INVERSE MAP WAS BROKEN WHERE IT MATTERED MOST. 15 of the 180 resolvable
citations are DIRECTORIES — `src/stackowl/commands`, `src/stackowl/sandbox`,
`src/stackowl/tools/code` and twelve more, across thirteen documents. `git log -- <dir>`
dated them correctly so the STALE verdict was right, but the culprit was computed with
`f in wanted`, an exact match against `--name-only` output, which never equals a
directory. Every one of those documents could only ever be reported `<- ?`, and D14.2
was, on the very run that found this. `doc_check` already names that failure in
`_changes_since`: *"a report that names a cause its detector did not use is worse than
one that names none"* — here it named NO cause while the detector had one.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "scripts"))

import doc_check  # noqa: E402
import docs_touching  # noqa: E402
from doc_check import _CITATION, _header, _resolve, _source_fields, cites  # noqa: E402

_DESIGNS = _ROOT / "docs" / "reference-mapping" / "designs"


def _cited_paths() -> list[str]:
    out: list[str] = []
    for doc in _DESIGNS.glob("*.md"):
        source = _source_fields(_header(doc.read_text(encoding="utf-8")))
        out.extend(
            r for raw in re.findall(_CITATION, source)
            if "/" in raw and (r := _resolve(raw))
        )
    return out


class TestOneRuleForWhatACitationCovers:
    @pytest.mark.tripwire
    def test_a_directory_citation_covers_the_files_under_it(self) -> None:
        """The defect, at unit scale."""
        assert cites("src/stackowl/commands/registry.py", "src/stackowl/commands")
        assert cites("src/stackowl/commands/registry.py", "src/stackowl/commands/")
        assert cites("src/stackowl/sandbox/bwrap.py", "src/stackowl/sandbox")

    @pytest.mark.tripwire
    def test_a_shared_PREFIX_is_not_containment(self) -> None:
        """The cheap way to write the fix — `changed.startswith(cited)` — makes
        `commands_registry.py` a child of `commands`. The separator is what makes it
        a path rule rather than a string rule."""
        assert not cites("src/stackowl/commands_other/x.py", "src/stackowl/commands")
        assert not cites("src/stackowl/commandsfoo.py", "src/stackowl/commands")

    @pytest.mark.tripwire
    def test_an_exact_file_citation_still_matches_only_itself(self) -> None:
        assert cites("src/stackowl/commands/registry.py", "src/stackowl/commands/registry.py")
        assert not cites("src/stackowl/commands/help_command.py",
                         "src/stackowl/commands/registry.py")

    @pytest.mark.tripwire
    def test_both_tools_ask_the_SAME_function(self) -> None:
        """Two copies of one path rule is the shape this repo pays for most, and the
        copy that drifts is always the one nobody runs. The report and the tool must
        be the same object, not two functions that agree today."""
        assert docs_touching.cites is doc_check.cites


class TestTheReportCanNameTheCulprit:
    @pytest.mark.tripwire
    def test_a_directory_citation_yields_a_named_culprit(self) -> None:
        """THE `<- ?` CASE. `_changes_since` over a directory citation must report
        WHICH cited path the commit reached, or the report is back to naming nothing."""
        changes = doc_check._changes_since(  # noqa: SLF001
            ["src/stackowl/commands"], "2020-01-01", None
        )
        assert changes, "no commit has ever touched src/stackowl/commands — implausible"
        assert any(touched for _d, _sha, _subj, touched in changes), (
            "every change to a directory citation came back with an empty culprit list — "
            "the report can only print `<- ?` for all thirteen such documents"
        )

    def test_the_corpus_really_does_cite_directories(self) -> None:
        """VACUITY CONTROL, and the denominator. If the corpus stopped citing
        directories, the two assertions above would pass over nothing."""
        cited = _cited_paths()
        dirs = [c for c in cited if (_ROOT / c).is_dir()]
        assert len(cited) >= 100, len(cited)
        assert len(dirs) >= 5, f"only {len(dirs)} directory citations in {len(cited)}"


class TestTheChangeCanNameItsDocuments:
    @pytest.mark.tripwire
    def test_it_answers_the_question_that_was_missed(self) -> None:
        """The regression test for `1897e0c3`. Both documents must come back — D14.1
        by an exact file citation, D14.2 only through its directory one, which is why
        this single assertion covers both halves of the item."""
        hits = docs_touching.documents_touching(["src/stackowl/commands/registry.py"])
        found = {doc for entries in hits.values() for doc, _v in entries}

        assert "D14.1.md" in found, "the exact citation is not resolved"
        assert "D14.2.md" in found, (
            "the DIRECTORY citation is not resolved — this is the half that was silent"
        )

    def test_it_reports_a_verification_date_a_reader_can_act_on(self) -> None:
        """Naming the document is half the answer; a reader needs to know how stale it
        already is to judge whether to re-read or dismiss."""
        hits = docs_touching.documents_touching(["src/stackowl/commands/registry.py"])
        for entries in hits.values():
            for doc, verified in entries:
                assert re.search(r"\d{4}-\d{2}-\d{2}", verified), (doc, verified)

    def test_a_file_no_document_declares_says_so_without_pretending(self) -> None:
        """A tool that reads declared headers only must never be read as speaking for
        the 35 documents that declare no Source. The empty answer is honest, and the
        caveat that goes with it is asserted rather than assumed."""
        hits = docs_touching.documents_touching(["scripts/docs_touching.py"])
        assert hits == {}
        src = (_ROOT / "scripts" / "docs_touching.py").read_text(encoding="utf-8")
        assert "no tool can speak for those" in src

    def test_it_runs_as_a_command_and_prints_the_documents(self) -> None:
        """Wired, not merely built — the shape this repo names most often. A helper
        nothing can invoke is decoration."""
        out = subprocess.run(
            [sys.executable, str(_ROOT / "scripts" / "docs_touching.py"),
             "src/stackowl/commands/registry.py"],
            capture_output=True, text=True, timeout=180, cwd=_ROOT,
        )
        assert out.returncode == 0, out.stderr[-800:]
        assert "D14.1.md" in out.stdout and "D14.2.md" in out.stdout, out.stdout[-800:]
