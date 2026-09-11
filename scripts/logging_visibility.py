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
#:
#: `webhooks` JOINED 2026-09-11 (DEBT-303) and the omission had a cost. The set is
#: hand-listed, and the criterion in the docstring — no user, no reply, no cost
#: record — fits an inbound webhook exactly: nobody is waiting on the reply and no
#: turn accounts for it. Because the package sat outside, its request path logged
#: at DEBUG unchallenged, and a claim in `receiver.py` then cited one of those
#: invisible lines as proof the path had never run.
#: A rule scoped to a hand-listed CONTAINER cannot see a subsystem nobody thought
#: to list. This addition does not fix that; it pays one instance of it.
BACKGROUND: tuple[str, ...] = (
    "scheduler", "parliament", "learning", "notifications", "objectives", "webhooks",
    "control_plane", "owls",
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


def fully_silent(root: pathlib.Path, packages: tuple[str, ...] | None) -> list[tuple[str, str, int]]:
    """Functions that log ONLY at DEBUG — quiet on EVERY path, not just one.

    THE BLIND SPOT THIS REPORT EXISTS FOR, and it was found by the defect it
    would have caught. `scan_tree` above looks for ASYMMETRY: loud on one return,
    quiet on another. A function with NO loud return is therefore invisible to
    it — and is strictly worse, because nothing it does is observable at all.

    MEASURED 2026-09-11: `owls/dna_injector.py::inject` appends a behavioural
    directive to an owl's system prompt — the one event that answers whether the
    whole DNA subsystem has ever changed how an owl behaves — and all three of its
    log calls were DEBUG. ESC-116 asked the operator to decide whether to RETIRE
    that subsystem on the claim that it "has never once changed how an owl
    behaves", and the line that would prove or refute it had fired zero times
    because it could not fire. The claim was unfalsifiable, and the guard built to
    catch exactly that shape could not see it.

    A REPORT, never a gate: 49 functions match across the background set, and a
    guard that fails 49 correct-looking things gets bypassed rather than
    satisfied — the same reason `doc_check` is not a tripwire. The count is what
    makes the class drainable.
    """
    out: list[tuple[str, str, int]] = []
    for path in sorted(root.rglob("*.py")):
        rel_parts = path.relative_to(root).parts
        if packages is not None and rel_parts[0] not in packages:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            levels = [
                n.func.attr
                for n in ast.walk(fn)
                if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and isinstance(n.func.value, ast.Attribute)
                and isinstance(n.func.value.value, ast.Name)
                and n.func.value.value.id == "log"
            ]
            if not levels or any(lv in LOUD for lv in levels):
                continue
            returns = sum(1 for n in ast.walk(fn) if isinstance(n, ast.Return))
            rel = str(path.relative_to(root.parent.parent))
            out.append((f"{rel}::{fn.name}", "", returns))
    return out


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

    silent = fully_silent(_SRC, packages)
    deciding = [s for s in silent if s[2] >= 2]
    print()
    print(f"QUIET ON EVERY PATH — {scope}")
    print(f"  {len(silent)} function(s) log ONLY at DEBUG, so nothing they do is "
          f"observable; {len(deciding)} of them have two or more returns, i.e. they "
          f"make a DECISION nobody can see.")
    print("  The report above finds ASYMMETRY and is blind to these by construction.")
    for key, _unused, returns in deciding:
        print(f"        {returns} returns   {key}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
