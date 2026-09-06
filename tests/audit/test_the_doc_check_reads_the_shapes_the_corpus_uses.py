"""The parser must read the documents that exist, not the ones the standard describes.

WHY THIS EXISTS, and it is the SECOND time in two loops that a parser — not a
rule — was the defect.

`doc_check.py` shipped last loop and reported **51 of 84 documents UNMEASURABLE**:
it could not date them, so their `Last verified` claim went unchecked. Read as a
property of the corpus that says most design documents are undocumented. Read
honestly, most of it was the parser:

  * **`Source (...)` VARIANTS.** Twelve documents write `Source (new):`,
    `Source (changed):`, `Source (to change):`, `Source (subject)`. The field
    regex was `[A-Za-z ]+`, so a parenthesis made the line invisible — D05.1
    carries a perfectly good `Last verified` AND two Source fields and was
    counted as having neither.
  * **src-RELATIVE PATHS.** Those same documents cite `tools/registry.py` and
    `providers/_resilient_round.py` — relative to `src/stackowl`, not the repo
    root — and the resolver only tried the root, so every path "did not exist".

MEASURED: 25 documents resolvable under the old rules, **36 under these**. The
unmeasurable population is a parser artefact by eleven documents.

WHAT THIS DOES NOT CLAIM. Thirty-four documents use a different genre entirely
(`**Item.** / **Ask.** / **Answer.**` prose, no blockquote header at all) and
stay unmeasurable — that is a real divergence between DOC_STANDARD and practice,
not a parser bug, and it is recorded rather than papered over. The fix here is
only for documents that DID follow the header convention and were misread.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "scripts"))

from doc_check import _header, _resolve, _source_fields  # noqa: E402


class TestTheFieldNameMayCarryAQualifier:
    def test_a_parenthesised_Source_is_still_a_Source(self) -> None:
        """THE DEFECT. `[A-Za-z ]+` made `Source (new):` invisible, so a document
        with a real header was filed as having none."""
        head = _header(
            "# T\n\n"
            "> **Status:** live\n"
            "> **Source (new):** `tools/a.py`\n"
            "> **Source (changed):** `tools/b.py`\n"
            "> **Last verified:** 2026-09-07, against commit `abc1234`\n\n"
            "## Why\n"
        )

        assert "Last verified" in head
        assert _source_fields(head), "no Source field was recognised"

    def test_every_Source_variant_is_collected_not_just_the_first(self) -> None:
        """A document that splits its sources across `(new)` and `(changed)` is
        describing both; dating it by one half would be arbitrary."""
        head = _header(
            "> **Source (new):** `tools/a.py`\n"
            "> **Source (changed):** `tools/b.py`\n"
        )
        joined = _source_fields(head)

        assert "a.py" in joined and "b.py" in joined

    def test_a_plain_Source_still_works(self) -> None:
        """No regression: the standard's own spelling is the common case."""
        head = _header("> **Source:** `src/stackowl/x.py`\n")

        assert "x.py" in _source_fields(head)


class TestAPathIsTriedWhereTheCorpusActuallyWritesIt:
    def test_a_repo_relative_path_resolves(self) -> None:
        assert _resolve("src/stackowl/db/pool.py") is not None

    def test_a_src_relative_path_resolves(self) -> None:
        """The twelve variant documents cite `tools/registry.py`, meaning
        `src/stackowl/tools/registry.py`. The resolver tried only the root, so
        every one of those paths "did not exist" and the document could not be
        dated by any of them."""
        assert _resolve("db/pool.py") is not None, (
            "a src/stackowl-relative path did not resolve — the shape twelve "
            "documents actually use"
        )

    def test_a_genuinely_absent_path_still_does_not_resolve(self) -> None:
        """The control. Widening a resolver until everything resolves would make
        staleness unfalsifiable."""
        assert _resolve("no/such/file_that_does_not_exist.py") is None


class TestTheMeasurableCorpusActuallyGrew:
    def test_more_documents_are_measurable_than_before(self) -> None:
        """POPULATION CONTROL. The widening is only worth anything if it moves
        documents from unmeasurable to measured — and the number is the claim."""
        import doc_check

        docs = sorted((_ROOT / "docs" / "reference-mapping" / "designs").glob("*.md"))
        measurable = 0
        for d in docs:
            head = _header(d.read_text(encoding="utf-8"))
            if not head.get("Last verified"):
                continue
            src = _source_fields(head)
            import re
            if any(_resolve(p) for p in re.findall(r"`([A-Za-z0-9_./-]+\.(?:py|sql|sh))`", src)):
                measurable += 1

        assert measurable >= 34, (
            f"only {measurable} documents are measurable; the widening measured 36"
        )
        assert doc_check.main() == 0
