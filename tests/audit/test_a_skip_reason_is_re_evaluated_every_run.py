"""A skip must be a CONDITION the runner re-checks, never a claim written once.

WHY THIS EXISTS, measured 2026-09-06.

`tests/journeys/test_j3_debug_script.py` carried an unconditional skip:

    @pytest.mark.skip(reason="E11 execute_code not shipped — ...")
    async def test_j3_reproduce_and_confirm_green_with_execute_code(...):
        \"\"\"...\"\"\"
        pytest.skip("E11 execute_code not shipped")

The claim was FALSE and had been for a long time: `execute_code` ships at
`tools/code/execute_code.py` with **42 recorded invocations** in
`task_outcomes`, and J11's six real-bwrap tests pass on this host. The function
beneath those two identical claims was a docstring and nothing else — empty
scaffolding "for later", which is what the retired-means-deleted rule forbids.

THE ROOT CAUSE IS THE SHAPE, NOT THE STALENESS. `@pytest.mark.skip` states a
fact about the world ONCE, at authoring time, and nothing ever asks again.
`@pytest.mark.skipif(<expr>)` states a CONDITION the runner re-evaluates on
every run, so it un-skips itself the moment the world changes. This programme
has already cured this exact disease twice, both times by making the claim
executable: `premise_check` for escalations whose premise aged silently, and
`closing_check` for `partial` stages that were dead ends rather than open
questions. An unconditional skip is the third instance, and it is the one that
sat inside the test suite where a stale claim looks like coverage.

WHAT THIS GUARD DOES NOT FORBID. Conditional skips are correct and there are
many: Windows-only mode bits, symlink support, ripgrep, Xvfb, a live browser,
bwrap viability. Every one of those re-evaluates. So does `pytest.skip()` called
after inspecting something at runtime. The line is drawn at skips that can never
change their mind.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_TESTS = Path(__file__).resolve().parents[1]


def _unconditional_skips() -> list[str]:
    """Every `@pytest.mark.skip(...)` decorator and bare module-level skip.

    Uses the AST, not a grep: `skipif` contains the substring `skip`, and a
    regex that tried to exclude it would also have to reason about line breaks
    inside the decorator's arguments.
    """
    found: list[str] = []
    for path in sorted(_TESTS.rglob("test_*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                continue
            for dec in node.decorator_list:
                target = dec.func if isinstance(dec, ast.Call) else dec
                if not isinstance(target, ast.Attribute):
                    continue
                # pytest.mark.skip -> Attribute(attr='skip', value=Attribute(attr='mark'))
                if target.attr != "skip":
                    continue
                if not (
                    isinstance(target.value, ast.Attribute) and target.value.attr == "mark"
                ):
                    continue
                rel = path.relative_to(_TESTS.parent).as_posix()
                found.append(f"{rel}::{node.name}")
    return found


@pytest.mark.tripwire
def test_no_test_is_skipped_unconditionally() -> None:
    """A test nothing can ever un-skip is not coverage; it is a claim about the
    world that stopped being checked."""
    offenders = _unconditional_skips()

    assert not offenders, (
        "these are skipped unconditionally, so no change in the world can ever "
        "run them — state the CONDITION with @pytest.mark.skipif(<probe>) so the "
        "runner re-evaluates it, or delete the test:\n  " + "\n  ".join(offenders)
    )


def test_the_guard_can_see_the_shape_it_forbids() -> None:
    """VACUITY CONTROL, and it is the one that matters here: the assertion above
    passes over an EMPTY list by design, so without this it would be
    indistinguishable from a walker that matches nothing at all."""
    sample = ast.parse(
        "import pytest\n"
        "@pytest.mark.skip(reason='x')\n"
        "def test_a(): ...\n"
        "@pytest.mark.skipif(True, reason='y')\n"
        "def test_b(): ...\n"
    )
    hits = []
    for node in ast.walk(sample):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            continue
        for dec in node.decorator_list:
            target = dec.func if isinstance(dec, ast.Call) else dec
            if (
                isinstance(target, ast.Attribute)
                and target.attr == "skip"
                and isinstance(target.value, ast.Attribute)
                and target.value.attr == "mark"
            ):
                hits.append(node.name)

    assert hits == ["test_a"], (
        f"the walker must catch skip and IGNORE skipif — it found {hits}"
    )


def test_conditional_skips_are_still_plentiful() -> None:
    """The other half of the control: if the suite had no skipif at all, the rule
    above would be trivially satisfiable by having no skips of any kind, and this
    file would be arguing about nothing."""
    n = sum(
        "skipif" in p.read_text(encoding="utf-8")
        for p in _TESTS.rglob("test_*.py")
    )

    assert n >= 5, f"only {n} files use skipif — is the suite still using conditions?"
