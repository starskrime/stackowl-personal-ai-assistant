#!/usr/bin/env python3
"""Which background subsystems can decline to act without leaving a record.

WHY THIS EXISTS. `CLAUDE.md` says it plainly — *"Production runs at INFO. A
`log.*.debug` line does not exist when you need it."* — and `item-loop/SKILL.md`
says it again. **Nothing enforced it.** MEASURED 2026-09-09 across every retained
log: 652,309 INFO, 13,547 WARNING, 11,250 ERROR, 2 CRITICAL and **zero DEBUG**.
A DEBUG line is not a faint signal in this deployment; it is no signal.

WHAT IT COST, TWICE. The rule was written after D08.1's fourth acceptance check
sat open for days because its only evidence line was DEBUG. It then happened
again, to the self-healing loop: `route_rca_verdict` logged its entry and its
`not verdict.verified` early return at DEBUG and its exit at INFO, so the router
was observable ONLY when it consumed a verdict. Its last INFO line is
2026-09-03; `incident_escalation: RCA complete` has fired 41 times since. A whole
session could not answer whether self-healing was DEAD or merely DECLINING,
because both write the same thing: nothing.

THE SHAPE, stated so it is checkable rather than remembered. A function is
**asymmetric** when one return path logs at INFO/WARNING/ERROR and another
return path's only log is DEBUG. Its outcome is then visible exactly when it
acts and invisible when it does not — which inverts what a reader needs, because
"it did nothing" is the answer that needs evidence.

SCOPED TO BACKGROUND PACKAGES, and that is a real boundary rather than a
convenient one. A per-turn tool that returns quietly is still observed: the turn
it belongs to has a user, a reply and a cost record. A scheduler tick has none of
those. The log is the only witness there is, so a quiet return in `scheduler/`,
`parliament/`, `learning/`, `notifications/` or `objectives/` erases the event.
The wider corpus is reported too, but only the background set is enforced.

    uv run python scripts/logging_visibility.py            # the background set
    uv run python scripts/logging_visibility.py --all      # every package
"""

from __future__ import annotations

import argparse
import ast
import collections
import pathlib
import sys

#: Levels this deployment actually writes. Anything outside it is unobservable.
LOUD: frozenset[str] = frozenset({"info", "warning", "error", "critical", "exception"})

#: The packages with no user attached to their work — see the module docstring.
BACKGROUND: tuple[str, ...] = (
    "scheduler", "parliament", "learning", "notifications", "objectives",
)

_SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "stackowl"


class Finding:
    """One asymmetric function."""

    def __init__(self, path: str, lineno: int, func: str,
                 loud: int, quiet: list[tuple[int, str]]) -> None:
        self.path = path
        self.lineno = lineno
        self.func = func
        self.loud = loud
        self.quiet = quiet

    @property
    def key(self) -> str:
        """Stable identity: repo-relative path plus function name.

        NOT the line number — a function that moves down a file is the same
        function, and an allowlist keyed on line numbers would expire on every
        unrelated edit above it and be re-approved without thought.
        """
        return f"{self.path}::{self.func}"

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Finding {self.key} loud={self.loud} quiet={len(self.quiet)}>"


def _log_level(node: ast.expr) -> str | None:
    """``log.<area>.<level>(...)`` -> ``level``, else ``None``.

    Deliberately narrow. It matches the named-logger convention this repo
    mandates (`log.tool`, `log.scheduler`, …) and nothing else, so a local
    variable that happens to be called `debug` is not mistaken for a log line.
    """
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return None
    inner = node.func.value
    if not isinstance(inner, ast.Attribute):
        return None
    base = inner.value
    if isinstance(base, ast.Name) and base.id == "log":
        return node.func.attr
    return None


def _first_arg(call: ast.expr) -> str:
    if isinstance(call, ast.Call) and call.args and isinstance(call.args[0], ast.Constant):
        return str(call.args[0].value)
    return ""


class _Walker:
    """Classify every ``return`` by the log statement that immediately precedes it.

    Walks STATEMENT LISTS rather than using `ast.walk`, because the question is
    positional: what was logged on the way out of *this* branch. `ast.walk`
    flattens the tree and would happily pair a return with a log line from a
    sibling branch it can never reach.
    """

    def __init__(self) -> None:
        self.loud = 0
        self.quiet: list[tuple[int, str]] = []

    def scan(self, body: list[ast.stmt]) -> None:
        preceding: tuple[str, int, str] | None = None
        for stmt in body:
            if isinstance(stmt, ast.Expr):
                level = _log_level(stmt.value)
                if level is not None:
                    preceding = (level, stmt.lineno, _first_arg(stmt.value)[:90])
                    continue
            if isinstance(stmt, ast.Return):
                if preceding is not None:
                    level, lineno, msg = preceding
                    if level in LOUD:
                        self.loud += 1
                    elif level == "debug":
                        self.quiet.append((lineno, msg))
                continue
            # A non-log, non-return statement breaks the adjacency: whatever was
            # logged above it is no longer "the record this return leaves".
            preceding = None
            for field in ("body", "orelse", "finalbody"):
                nested = getattr(stmt, field, None)
                if isinstance(nested, list):
                    self.scan(nested)
            for handler in getattr(stmt, "handlers", []) or []:
                self.scan(handler.body)


def scan_tree(root: pathlib.Path, packages: tuple[str, ...] | None) -> list[Finding]:
    """Every asymmetric function under *root*, optionally limited to *packages*."""
    found: list[Finding] = []
    for path in sorted(root.rglob("*.py")):
        if packages is not None and path.relative_to(root).parts[0] not in packages:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        rel = str(path.relative_to(root.parents[1]))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            walker = _Walker()
            walker.scan(node.body)
            if walker.loud and walker.quiet:
                found.append(Finding(rel, node.lineno, node.name,
                                     walker.loud, walker.quiet))
    return found


def background_findings() -> list[Finding]:
    """The enforced set — what the tripwire ratchets against."""
    return scan_tree(_SRC, BACKGROUND)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true",
                        help="report every package, not only the background set")
    args = parser.parse_args(argv)

    packages = None if args.all else BACKGROUND
    findings = scan_tree(_SRC, packages)
    scope = "every package" if args.all else "background packages"

    print(f"AN OUTCOME VISIBLE ONLY WHEN IT ACTS — {scope}")
    print(f"  {len(findings)} function(s) log loudly on one return path and only "
          f"at DEBUG on another.")
    print("  Production writes no DEBUG at all, so the quiet branch leaves no record.")
    print()
    by_pkg = collections.Counter(f.path.split("/")[2] for f in findings)
    for pkg, n in by_pkg.most_common():
        print(f"  {n:>4}  {pkg}")
    print()
    for f in findings:
        print(f"{f.path}:{f.lineno} {f.func}()  loud_returns={f.loud}")
        for lineno, msg in f.quiet:
            print(f"        DEBUG@{lineno}  {msg}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
