"""A `file.py::Symbol` citation is stale only when THAT SYMBOL changed.

WHY THIS EXISTS. `doc_check._resolve` threw the symbol away — `path.split("::", 1)[0]`
— and its comment gave the reason: *"The file is the thing git can date."* That is
false, and the cost was measurable.

MEASURED 2026-09-08. `execute.py` is 3,960 lines and is named as a `Source:` by SIX
design documents; `registry.py` is 895 lines and named by FOUR. Commit `bf603ef7`
changed TEN LINES inside `build_tool_schemas` and marked EIGHT documents stale. One of
them, D09.4, cites `execute.py::_turn_context_prefix` — lines 1225-1331 against a
commit that touched 1634-1643, three hundred lines away. The detector had been handed
the exact information that proved the document was fine and discarded it on the way in.

A convention with no reader is the write-with-no-reader shape wearing a citation: the
corpus already carried 28 symbol-level citations across 16 documents, and nothing read
one of them.

AND THE FIRST MEASUREMENT OF THE FIX WAS VACUOUS, which is why the denominator test
below exists. Loading the patched module put `_DESIGNS` under the scratch copy, so the
comparison globbed an EMPTY directory and reported "8 stale -> 0 stale". Nothing was
fixed; nothing had been looked at. Pointed at the real corpus the answer is 8 -> 7.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]

#: The commit this whole item was measured on. A real commit in this repository's
#: history, pinned deliberately: a synthetic fixture cannot show that the detector was
#: wrong about a document somebody actually wrote.
_COMMIT = "bf603ef7"
_FILE = "src/stackowl/pipeline/steps/execute.py"

#: Cited by D09.4 and NOT touched by `_COMMIT` — the false stale this item removes.
_UNTOUCHED_SYMBOL = "_turn_context_prefix"
#: Where `_COMMIT` actually landed.
_TOUCHED_SYMBOL = "build_tool_schemas"


def _doc_check():
    """Import `scripts/doc_check.py` and ASK it, never restate its logic here."""
    path = _ROOT / "scripts" / "doc_check.py"
    spec = importlib.util.spec_from_file_location("_doc_check_symbol_probe", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_doc_check_symbol_probe"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def dc():
    return _doc_check()


@pytest.mark.tripwire
def test_a_commit_elsewhere_in_the_file_does_not_reach_the_cited_symbol(dc) -> None:
    """THE DEFECT, on the document that suffered it."""
    assert not dc._commit_reaches_symbols(  # noqa: SLF001
        _COMMIT, _FILE, frozenset({_UNTOUCHED_SYMBOL})
    ), (
        f"{_COMMIT} is reported as reaching {_UNTOUCHED_SYMBOL}, which it does not "
        f"touch; D09.4 goes on being marked stale by a change 300 lines away"
    )


@pytest.mark.tripwire
def test_the_commit_DOES_reach_the_symbol_it_actually_changed(dc) -> None:
    """POSITIVE CONTROL. A filter that answers 'no' to everything passes the test
    above and silences every real staleness in the corpus — which is the far worse
    failure, because a document reported fresh is one nobody looks at again."""
    assert dc._commit_reaches_symbols(  # noqa: SLF001
        _COMMIT, _FILE, frozenset({_TOUCHED_SYMBOL})
    ), (
        f"{_COMMIT} changed {_TOUCHED_SYMBOL} and the filter says it did not; real "
        f"staleness is now being hidden"
    )


@pytest.mark.tripwire
def test_a_symbol_that_cannot_be_located_counts_as_reached(dc) -> None:
    """UNKNOWN MEANS STALE — the direction this whole filter has to fail in.

    A renamed, moved, or not-yet-existing symbol must make the commit COUNT. Silence
    is the dangerous answer here: a false 'stale' costs a re-read, a false 'fresh'
    costs a document asserting something untrue with nothing left to notice.
    """
    assert dc._commit_reaches_symbols(  # noqa: SLF001
        _COMMIT, _FILE, frozenset({"a_symbol_that_has_never_existed"})
    )
    assert dc._commit_reaches_symbols(_COMMIT, _FILE, frozenset())  # noqa: SLF001


def test_the_span_is_read_at_that_commit_not_in_todays_tree(dc) -> None:
    """A symbol MOVES. Asking today's line numbers about an old diff compares two
    different files, so the span must come from `git show <sha>:<path>`."""
    span = dc._symbol_span(_COMMIT, _FILE, _UNTOUCHED_SYMBOL)  # noqa: SLF001
    assert span is not None and span[0] < span[1]
    assert dc._symbol_span(_COMMIT, _FILE, "not_a_symbol") is None  # noqa: SLF001


def test_a_path_cited_bare_anywhere_keeps_whole_file_dating(dc) -> None:
    """D05.4 cites `pipeline/steps/execute.py` BARE and
    `tools/registry.py::to_provider_schema` with a symbol. The bare citation is a
    claim about the whole file; letting the precise one narrow it would silence a
    real staleness on the strength of an unrelated citation."""
    mixed = dc._cited_symbols([  # noqa: SLF001
        "pipeline/steps/execute.py",
        "pipeline/steps/execute.py::_turn_context_prefix",
    ])
    assert not mixed, f"a bare citation was overridden by a symbol one: {mixed}"

    only_symbol = dc._cited_symbols(["pipeline/steps/execute.py::_turn_context_prefix"])  # noqa: SLF001
    assert only_symbol, "a path cited ONLY by symbol is not being narrowed at all"


@pytest.mark.tripwire
def test_the_corpus_actually_carries_symbol_citations(dc) -> None:
    """0 OVER 0 IS NOT A PASS, and this file has already been fooled once: the first
    measurement of this fix globbed an empty scratch directory and reported the stale
    count falling from 8 to 0. It had looked at nothing."""
    designs = sorted((_ROOT / "docs" / "reference-mapping" / "designs").glob("*.md"))
    assert len(designs) > 50, f"only {len(designs)} design documents found"

    import re

    with_symbols = 0
    for d in designs:
        head = dc._header(d.read_text(encoding="utf-8"))  # noqa: SLF001
        cited = [
            p for p in re.findall(dc._CITATION, dc._source_fields(head))  # noqa: SLF001
            if "/" in p
        ]
        if dc._cited_symbols(cited):  # noqa: SLF001
            with_symbols += 1
    assert with_symbols >= 3, (
        f"only {with_symbols} document(s) have a narrowable symbol citation across "
        f"{len(designs)} documents; the feature has no corpus and every assertion "
        f"above is about a case that does not occur"
    )
