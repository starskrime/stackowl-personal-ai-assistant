"""The stale report names the SYMBOLS a commit touched, not just the file.

WHY THIS EXISTS. `doc_check` printed `<- registry.py` and a commit subject, then
told the reader to "check whether the change even concerned them". Performing that
check means reading the file at that commit, walking its AST, reading the commit's
hunks and intersecting them — and I did exactly that BY HAND in three consecutive
loops, for `1f48d999`, `bf603ef7` and `d5f2f26e`.

AND THE MACHINERY WAS ALREADY THERE. DEBT-241 shipped `_symbol_span` and
`_changed_line_ranges` to date SYMBOL-CITED paths. They had exactly one caller,
`_commit_reaches_symbols`, which runs only when a citation names a symbol. For a
BARE citation — how all 69 citations of files >=500 lines are written, and where
the whole drain comes from — they computed nothing. Built, and not wired to the
case that needed it.

WHAT IT IS WORTH. `execute.py` is 3,961 lines cited bare by six documents;
`orchestrator.py` is 4,840 cited by five. `d5f2f26e` was a DOCSTRING edit to
`to_provider_schema` and marked four documents stale; D05.1 mentions that function
once and `register`/`discover` thirty-seven times between them. The report now says
`registry.py (ToolRegistry, to_provider_schema)` and that dismissal takes seconds.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]

#: Real commits in this repository, with the symbol sets DERIVED BY HAND in earlier
#: loops before this code existed. A synthetic fixture would only prove the function
#: agrees with itself; these are independent answers it has to reproduce.
_HAND_DERIVED = [
    (
        "bf603ef7",
        "src/stackowl/tools/registry.py",
        {"ToolRegistry", "_fit_envelope_to_window", "to_provider_schema"},
    ),
    (
        "bf603ef7",
        "src/stackowl/pipeline/steps/execute.py",
        {"_run_with_tools", "build_tool_schemas"},
    ),
    (
        "d5f2f26e",
        "src/stackowl/tools/registry.py",
        {"ToolRegistry", "to_provider_schema"},
    ),
]


def _doc_check():
    """Import `scripts/doc_check.py` and ASK it, never restate its logic."""
    path = _ROOT / "scripts" / "doc_check.py"
    spec = importlib.util.spec_from_file_location("_doc_check_touched_probe", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_doc_check_touched_probe"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def dc():
    return _doc_check()


@pytest.mark.tripwire
@pytest.mark.parametrize(("sha", "path", "expected"), _HAND_DERIVED)
def test_it_reproduces_an_answer_derived_by_hand(dc, sha, path, expected) -> None:
    """THE EVIDENCE. Three real commits whose symbol sets were worked out manually
    across three loops, before this function existed."""
    assert set(dc._touched_symbols(sha, path)) == expected  # noqa: SLF001


def test_the_innermost_symbol_is_named_first(dc) -> None:
    """A reader wants `build_tool_schemas`, not the 1,900-line `_run_with_tools`
    that happens to contain it. Both are true; only one is useful first."""
    got = dc._touched_symbols(  # noqa: SLF001
        "bf603ef7", "src/stackowl/pipeline/steps/execute.py"
    )
    assert got[0] == "build_tool_schemas", got


@pytest.mark.tripwire
def test_it_NAMES_and_never_FILTERS(dc) -> None:
    """THE FAILURE MODE THIS MUST NOT HAVE.

    A symbol a document never mentions can still be the one that broke it, so this
    addition is presentational: it must not change WHICH documents are reported
    stale. The staleness verdict comes from `_changes_since`, and `_touched_symbols`
    is not called from it — asserted here rather than assumed, because turning a
    report into a filter is exactly how a detector goes quiet.
    """
    import inspect

    source = inspect.getsource(dc._changes_since)  # noqa: SLF001
    assert "_touched_symbols" not in source, (
        "`_touched_symbols` is being consulted while deciding staleness; it is "
        "meant to describe a change, not to suppress one"
    )


def test_an_unreadable_case_says_nothing_rather_than_guessing(dc) -> None:
    """Empty means 'no extra information', which is the honest answer for a
    non-Python source, a file absent at that commit, or an unreadable diff. It must
    never be an exception, because this runs inside a report the loop depends on."""
    assert dc._touched_symbols("d5f2f26e", "docs/reference-mapping/PROCESS.md") == ()  # noqa: SLF001
    assert dc._touched_symbols("d5f2f26e", "src/stackowl/paths.py") == ()  # noqa: SLF001
    assert dc._touched_symbols("d5f2f26e", "src/stackowl/does_not_exist.py") == ()  # noqa: SLF001
    assert dc._touched_symbols("0000000", "src/stackowl/tools/registry.py") == ()  # noqa: SLF001


@pytest.mark.tripwire
def test_the_symbol_table_is_read_at_that_commit(dc) -> None:
    """0 OVER 0 IS NOT A PASS. Every assertion above is satisfied by a function that
    returns () for everything, so the corpus of symbols must be shown to be real."""
    spans = dc._symbols_at("d5f2f26e", "src/stackowl/tools/registry.py")  # noqa: SLF001
    assert len(spans) > 20, f"only {len(spans)} symbols found in registry.py"
    names = {n for n, _, _ in spans}
    assert {"to_provider_schema", "ToolRegistry"} <= names
    assert all(lo <= hi for _, lo, hi in spans)
