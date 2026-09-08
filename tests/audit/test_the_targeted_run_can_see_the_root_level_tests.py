"""A targeted test run picked by PACKAGE cannot reach the 69 files in `tests/`.

THE COST, measured the day this was written. DEBT-228 changed
`JobScheduler.recover`. The targeted run was `tests/scheduler` + `tests/owls` —
chosen the way targeted paths are always chosen, by what the change looks related
to. `tests/test_story_7_1b.py` asserts `recover`'s contract and sits DIRECTLY in
`tests/`, so no package path touches it. The item shipped green, `tripwires.sh`
passed (it runs only `@pytest.mark.tripwire`), and 37 minutes later::

    FAILED tests/test_story_7_1b.py::TestSchedulerLifecycle::
           test_recover_advances_next_run_when_replay_disabled
    1 failed, 12553 passed, 17 skipped in 2239.34s   (rc=1, SUITE TREE STILL)

THE LANDMINE WAS ALREADY WRITTEN DOWN, in this repo's own instructions: "`tests/
<package>` NEVER runs `tests/*.py`. 69 test files sit directly in `tests/` … and
no package path touches one of them." I had read it, and it did not help — because
a warning names the HAZARD and the reader needs the ANSWER. Knowing that one of 69
files might matter is not knowing which one, and the gap between those two is
where the thirty-seven minutes went.

So `scripts/tests_touching.py` answers it from the tree, and this pins the one
property that failure turned on: the discovery must REACH files directly under
`tests/`. A version that walked packages only would return a confident,
incomplete list — which is worse than no tool, because it reads as coverage.

WHY A GATE AND NOT A NOTE. The property is binary and cheap: either the walk sees
root-level files or it does not. It cannot cry wolf on unrelated work, and it
fires exactly on the change that would re-create the blind spot.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]


def _tool():
    """Import `scripts/tests_touching.py` and ASK it, rather than restating it."""
    path = _ROOT / "scripts" / "tests_touching.py"
    spec = importlib.util.spec_from_file_location("_tests_touching_for_test", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_tests_touching_for_test"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.tripwire
def test_the_walk_reaches_tests_that_sit_directly_in_tests() -> None:
    """THE EXACT MISS. `scheduler.py` -> the root-level file that went red."""
    hits = _tool().covering_tests(["src/stackowl/scheduler/scheduler.py"])
    covering = hits["src/stackowl/scheduler/scheduler.py"]

    root_level = [t for t in covering if t.count("/") == 1]
    assert root_level, (
        "the discovery returned only package-scoped tests, which is the blind "
        "spot it exists to close: a change to the scheduler had a covering test "
        "directly in tests/ and a `tests/scheduler` run could never reach it"
    )
    assert "tests/test_story_7_1b.py" in covering, (
        "tests/test_story_7_1b.py no longer appears for scheduler.py. It is the "
        "case this tool was built on — if it moved, re-point this assertion at "
        "whatever root-level file now covers the scheduler rather than deleting "
        "the check"
    )


@pytest.mark.tripwire
def test_a_dotted_name_in_a_STRING_is_found_too() -> None:
    """Imports alone are not enough in this tree.

    Tests here monkeypatch by dotted string and read `inspect.getsource` of
    modules they never import. A discovery tool that followed only `import`
    statements would hand back a list that looks complete and is not — the
    "a grep returning zero proves the pattern did not match" failure, wearing a
    tool's clothes.
    """
    mod = _tool()
    covering = mod.covering_tests(["src/stackowl/config/test_mode.py"])[
        "src/stackowl/config/test_mode.py"
    ]
    assert covering, "no test covers test_mode.py, which several monkeypatch by string"

    # At least one of them must reach it WITHOUT importing it — that is the half
    # an import-only walk loses.
    import ast

    by_string_only = []
    for rel in covering:
        tree = ast.parse((_ROOT / rel).read_text(encoding="utf-8"))
        if "stackowl.config.test_mode" not in mod._imports(tree) and any(  # noqa: SLF001
            s.startswith("stackowl.config.test_mode") for s in mod._string_mentions(tree)  # noqa: SLF001
        ):
            by_string_only.append(rel)
    assert by_string_only, (
        "every covering test imports the module outright, so this assertion is "
        "no longer exercising the string-mention path — find a module that is "
        "patched by dotted name and point it there"
    )


def test_a_changed_SCRIPT_maps_to_the_tests_that_import_it() -> None:
    """`scripts/` WAS A BLIND SPOT, and the suite has already paid for it.

    Asked about `scripts/doc_check.py` this tool used to answer *"no changed files
    under src/ — nothing to map"* — a confident, empty answer about a file that
    `tests/audit` imports fourteen times over. Editing `scripts/doc_check.py`
    mid-run VOIDED THE 17th FULL SUITE for exactly that reason: the run's
    fingerprint covers `src/` and `tests/`, so it could not see the edit, and the
    instrument built so the reader would not have to remember which tests matter
    could not see it either.

    A tool that answers "nothing" for a whole class of file teaches the reader to
    stop asking, which is worse than having no tool.
    """
    mod = _tool()
    hits = mod.covering_tests(["scripts/doc_check.py"])
    covering = hits["scripts/doc_check.py"]

    assert len(covering) >= 10, (
        f"only {len(covering)} test(s) mapped to scripts/doc_check.py; tests/audit "
        f"imports it by bare name after a sys.path insert and by file path for "
        f"`spec_from_file_location`, and both must be found: {covering}"
    )
    assert all(t.startswith("tests/") for t in covering)


def test_a_script_stem_is_matched_EXACTLY_not_by_substring() -> None:
    """THE FAILURE MODE OF THE CURE.

    Scripts are matched on their stem, and a loose match would be worse than no
    match: a stem like `settings` appears in the prose of half this suite, and a
    tool that returns 400 files has told the reader nothing. `_script_stem` also
    has to REFUSE what is not a script, or a `src/` path would be mapped twice
    under two different rules.
    """
    mod = _tool()
    assert mod._script_stem("scripts/doc_check.py") == "doc_check"  # noqa: SLF001
    assert mod._script_stem("src/stackowl/config/settings.py") is None  # noqa: SLF001
    assert mod._script_stem("scripts/tripwires.sh") is None  # noqa: SLF001
    assert mod._script_stem("scripts/boundaries/thing.py") is None  # noqa: SLF001

    # NEGATIVE CONTROLS, and they are the whole test. MEASURED 2026-09-08: seven
    # test files mention `doc_check` ONLY in prose, so a substring match returns
    # 19 files where an exact match returns 14. The first draft of this assertion
    # used a count threshold on `map_check` and SURVIVED MUTATION — substring
    # matching passed it — because that stem happens not to over-match. A
    # threshold guesses; naming the files that must NOT appear does not.
    selected = set(mod.covering_tests(["scripts/doc_check.py"])["scripts/doc_check.py"])
    for prose_only in (
        "tests/memory/test_the_bound_cannot_delete_what_recall_can_find.py",
        "tests/infra/test_a_second_process_follows_the_rotation.py",
    ):
        assert prose_only not in selected, (
            f"{prose_only} names `doc_check` only inside a docstring and was "
            f"selected anyway — the stem is being matched inside prose, which "
            f"would hand the reader an unrelated package to run"
        )
    assert len(selected) >= 10, (
        "the negative controls above pass vacuously if nothing is selected at all"
    )
