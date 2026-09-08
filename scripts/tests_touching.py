#!/usr/bin/env python3
"""Which test files cover the source files you just changed — root-level ones included.

WHY THIS EXISTS, and it cost a red suite the day it was written. DEBT-228 changed
`JobScheduler.recover`. The targeted run was `tests/scheduler` + `tests/owls`,
chosen the way targeted paths are always chosen — by what the change looks related
to. `tests/test_story_7_1b.py` asserts `recover`'s contract and sits DIRECTLY in
`tests/`, so no package path touches it. The item shipped green, the tripwire gate
passed (it runs only `@pytest.mark.tripwire`), and 37 minutes of full suite later:

    FAILED tests/test_story_7_1b.py::TestSchedulerLifecycle::
           test_recover_advances_next_run_when_replay_disabled
    1 failed, 12553 passed, 17 skipped in 2239.34s

THE LANDMINE WAS ALREADY WRITTEN DOWN — "`tests/<package>` NEVER runs
`tests/*.py`; 69 test files sit directly in `tests/`" — and it did not help,
because a warning cannot tell you WHICH of the 69 is the one. Knowing the hazard
and knowing the answer are different things, and only the second is actionable.

So this asks the tree instead of asking the reader to remember:

    uv run python scripts/tests_touching.py                    # what you changed
    uv run python scripts/tests_touching.py src/stackowl/x.py  # or name the files

IT MATCHES THREE WAYS, and the second and third are not optional in this codebase.
An `import` alone would have missed nothing here, but this tree wires things
dynamically and asserts on source TEXT: tests monkeypatch by dotted string
("stackowl.config.test_mode.TestModeGuard.assert_not_test_mode") and read
`inspect.getsource` of modules they never import. A discovery tool that only
followed imports would hand back a confident, incomplete list — the shape this
programme pays for most.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_TESTS = _ROOT / "tests"
_SRC = _ROOT / "src"


def _changed_files() -> list[str]:
    """Working-tree + staged changes against HEAD, python only."""
    out: set[str] = set()
    for args in (["git", "diff", "--name-only", "HEAD"], ["git", "diff", "--name-only", "--cached"]):
        try:
            res = subprocess.run(args, capture_output=True, text=True, timeout=60, cwd=_ROOT)
        except Exception as exc:  # pragma: no cover — git absent
            print(f"  (could not read changes: {exc})", file=sys.stderr)
            continue
        out.update(line for line in res.stdout.split("\n") if line.endswith(".py"))
    return sorted(out)


def _module_name(path: str) -> str | None:
    """`src/stackowl/a/b.py` -> `stackowl.a.b`, or None if it is not a src path.

    THE FIRST VERSION ALSO RETURNED THE PARENT PACKAGES and justified it in prose
    — "`from stackowl.scheduler import scheduler` would otherwise be missed". That
    was FALSE ABOUT ITS OWN CODE: `_imports` already expands an `ImportFrom` into
    `stackowl.scheduler` AND `stackowl.scheduler.scheduler`, so the full name
    always matches, and the caller then took `max(..., key=len)` — the full name —
    and never read the parents at all. Mutation testing found it: removing them
    changed nothing, which is the definition of dead. Deleted with the claim.
    """
    p = Path(path)
    try:
        rel = p.relative_to("src") if p.parts[0] == "src" else p
    except ValueError:  # pragma: no cover — not a src path
        return None
    parts = list(rel.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts) if parts else None


def _imports(tree: ast.Module) -> set[str]:
    """Every dotted module name this file imports."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.add(node.module)
            names.update(f"{node.module}.{a.name}" for a in node.names)
    return names


def _string_mentions(tree: ast.Module) -> set[str]:
    """Dotted names appearing in STRING LITERALS — monkeypatch targets and the like."""
    return {
        n.value for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
        and n.value.startswith("stackowl.")
    }


def covering_tests(changed: list[str]) -> dict[str, list[str]]:
    """changed source file -> the test files that import or name its module."""
    wanted = {c: m for c in changed if (m := _module_name(c))}
    hits: dict[str, list[str]] = {c: [] for c in wanted}
    for test in sorted(_TESTS.rglob("test_*.py")):
        try:
            tree = ast.parse(test.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover — the syntax gate catches these
            continue
        imported = _imports(tree)
        mentioned = _string_mentions(tree)
        for src_file, module in wanted.items():
            # Imported outright, imported as a symbol FROM it, or named in a
            # string (monkeypatch targets, `inspect.getsource` by dotted path).
            if module in imported or module in mentioned or any(
                name.startswith(module + ".") for name in imported | mentioned
            ):
                hits[src_file].append(str(test.relative_to(_ROOT)))
    return hits


def main() -> int:
    changed = sys.argv[1:] or _changed_files()
    src_changed = [c for c in changed if c.startswith("src/")]
    if not src_changed:
        print("no changed files under src/ — nothing to map")
        return 0
    hits = covering_tests(src_changed)

    every: set[str] = set()
    for src_file, tests in sorted(hits.items()):
        print(f"\n{src_file}  ({len(tests)} test files)")
        for t in tests:
            # ROOT-LEVEL FILES ARE MARKED, because they are the ones a
            # `tests/<package>` run cannot reach and the reason this script exists.
            root_level = t.count("/") == 1
            print(f"  {'* ' if root_level else '  '}{t}")
        every.update(tests)

    roots = sorted(t for t in every if t.count("/") == 1)
    print(f"\n{len(every)} test files in total; {len(roots)} sit directly in tests/ "
          f"and NO `tests/<package>` path reaches them:")
    for t in roots:
        print(f"  {t}")
    if every:
        print("\nuv run pytest " + " ".join(sorted(every)) + " -q")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
