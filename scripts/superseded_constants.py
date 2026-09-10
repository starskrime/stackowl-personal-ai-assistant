#!/usr/bin/env python3
"""Prose that states a value its own constant no longer has.

WHY THIS EXISTS. `CLAUDE.md` is built around one sentence — *"Correcting one copy
of a rule is not correcting the rule"* — and it has been paid for three times at
the level of FILES: the suite-hangs claim survived in `SKILL.md` after `CLAUDE.md`
was fixed, then in `D04.1.md` and `SESSION_PROMPT.md` after a census of five, and
`d8b8ba81` left D05.7 advertising a deleted flag for eight days. Each cure widened
the SET of files swept.

**THE SECOND COPY CAN BE INSIDE ONE FILE, and no sweep of files can see that.**
MEASURED 2026-09-09: `db_reclaim.py` states its run-history retention window in
two rationale blocks — the `#:` comment on `_RUN_HISTORY_RETENTION_DAYS`, and the
docstring of `_prune_run_history`, the method that performs the deletion. Commit
`c628d1bf` tightened the window from 100 days to 7 on the operator's authority and
rewrote the first. The second still read *"At 100 days this deletes ZERO rows
today"* and *"Tightening it is a data-deletion decision that belongs to the
operator, not to this loop … Escalated with those numbers rather than taken."*

Eleven prune events had by then deleted his rows — 3,909, 4,087 and 4,151 on the
last three passes, each logging `retention_days: 7`. A reader auditing what this
platform deletes would have read the method that does the deleting and concluded
it deletes nothing. That is the worst direction for this particular claim to be
wrong in.

HOW A SUPERSEDED VALUE IS TOLD FROM A MEASUREMENT. Prose in this tree is full of
numbers, and nearly all of them are evidence rather than settings: *"19,285 pages
sat free"*, *"384,429,704 tokens"*, *"the oldest row was 92 days old"*. A detector
that flagged every figure sharing a constant's unit reports **38** constants here
and is useless. The discriminator is GIT: a number is suspicious only when the
constant ITSELF once held it. That takes the same corpus from 38 to **2**, and
both are worth a human read.

IT CANNOT DECIDE THE LAST STEP, and does not pretend to. Two innocent shapes
survive the git filter and neither can be told from a missed edit by machine:

  * HISTORY KEPT ON PURPOSE. `db_reclaim.py` says *"IT SHIPPED AT 100 FIRST,
    deliberately"* because that sequence is the justification for the current
    value.
  * A COLLISION. `curated.py`'s flagged line is a row of a measured traffic
    table — *"2026-08-11  5 boots  10 turns"* — where 10 is what was OBSERVED
    and happens to equal a value the constant once held. The filter removes
    almost every measurement in this tree and cannot remove one that collides.

That second class was found by misjudging it: the exemption was first recorded
against *"WAS 10 — the reference platform's default"*, the sentence the file
makes obvious, which is not the line this tool reported. **Read the line the
tool names.** So this prints evidence and a judgement is recorded beside it in
`tests/audit/test_a_tunable_states_its_value_in_one_place.py`, which fails on a
NEW site by name — the ratchet shape `logging_visibility.py` already uses.

WHAT IT SCANS, stated because a silent detector and a clean tree look identical.
COMMENTS and DOCSTRINGS only, never code. A hardcoded window inside a SQL string
is a real defect of a neighbouring kind and this tool does not claim to find it.
The denominator is printed for the same reason.

    uv run python scripts/superseded_constants.py
    uv run python scripts/superseded_constants.py --verbose   # the denominator
"""

from __future__ import annotations

import argparse
import ast
import io
import pathlib
import re
import subprocess
import sys
import tokenize
from dataclasses import dataclass

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_SRC = _ROOT / "src"
_TESTS = _ROOT / "tests"

#: Name suffix -> the word the prose uses for that unit. A constant whose name
#: ends in nothing recognisable is skipped: without a unit there is no way to
#: tell "7" the setting from "7" the anecdote, and guessing is how a detector
#: earns the distrust that gets it switched off.
UNITS: dict[str, str] = {
    "DAYS": "days", "HOURS": "hours", "MINUTES": "minutes", "SECONDS": "seconds",
    "SECS": "seconds", "MS": "ms", "PAGES": "pages", "ROWS": "rows",
    "CHARS": "chars", "TOKENS": "tokens", "BYTES": "bytes", "MB": "MB",
    "TURNS": "turns", "ATTEMPTS": "attempts", "RETRIES": "retries",
}


@dataclass(frozen=True)
class Site:
    """One prose mention of a value the constant used to have."""

    path: str
    const: str
    current: float
    stated: float
    unit: str
    lineno: int
    sha: str

    @property
    def key(self) -> str:
        """Stable identity for the ratchet: file, constant, and stated value.

        DELIBERATELY NOT THE LINE NUMBER. A judgement recorded against a line
        expires the moment anything above it is edited, and an exemption that
        expires silently is the failure this repo keeps finding.
        """
        return f"{self.path}::{self.const}::{_fmt(self.stated)}"


def _fmt(value: float) -> str:
    return str(int(value)) if value == int(value) else str(value)


def _module_constants(tree: ast.Module) -> dict[str, tuple[float, int]]:
    """Module-level ``NAME = <number>`` assignments, by name."""
    out: dict[str, tuple[float, int]] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        value = node.value
        if (
            isinstance(target, ast.Name)
            and isinstance(value, ast.Constant)
            and isinstance(value.value, (int, float))
            and not isinstance(value.value, bool)
        ):
            out[target.id] = (float(value.value), node.lineno)
    return out


def prose_spans(source: str, tree: ast.Module) -> list[tuple[int, str]]:
    """``(first line, text)`` for every comment and docstring in a module.

    PROSE, NOT CODE. The claim this tool makes is about what a reader is TOLD,
    so a number inside an expression is out of scope however wrong it is.
    """
    spans: list[tuple[int, str]] = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type == tokenize.COMMENT:
                spans.append((tok.start[0], tok.string))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        # A file this cannot tokenise is reported as unreadable by the caller
        # rather than silently contributing zero findings.
        raise
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
            continue
        doc = ast.get_docstring(node, clean=False)
        if doc is None:
            continue
        first = node.body[0]
        spans.append((first.lineno, doc))
    return spans


def stated_values(spans: list[tuple[int, str]], unit: str,
                  exclude: float) -> dict[float, list[int]]:
    """Every ``<number> <unit>`` in the prose that is not the current value."""
    pattern = re.compile(rf"\b(\d[\d,_.]*)\s+{re.escape(unit)}\b", re.IGNORECASE)
    found: dict[float, list[int]] = {}
    for start, text in spans:
        for match in pattern.finditer(text):
            raw = match.group(1).replace(",", "").replace("_", "").rstrip(".")
            try:
                number = float(raw)
            except ValueError:
                continue
            if number == exclude:
                continue
            line = start + text[: match.start()].count("\n")
            found.setdefault(number, []).append(line)
    return found


def past_values(path: str, const: str) -> dict[float, str]:
    """Values this constant has held, mapped to the commit that introduced each.

    ``-G``, NOT ``-S``, and the difference is the whole tool. ``-S`` reports
    commits where the NUMBER OF OCCURRENCES of the string changed, so it sees
    the commit that introduced a constant and is blind to every commit that only
    changed its VALUE — the exact event this tool exists to trace. Against
    ``_RUN_HISTORY_RETENTION_DAYS`` ``-S`` returns one commit and ``-G`` returns
    two, and the one it misses is `c628d1bf`, the 100-to-7 change. A constant
    edited twice would have hidden its middle value entirely.

    Caught by this tool's own control test rather than by reading: the scan
    still reported the stale site, because the value it needed happened to be
    the constant's FIRST. Both are true and only one is robust.

    Reads a handful of blobs per constant rather than the file's whole history.
    A blob that cannot be read (a rename, a deletion) is skipped: a missing
    revision must never become a finding.
    """
    log = subprocess.run(
        ["git", "log", "--format=%H", "-G", rf"^\s*{re.escape(const)}\s*=",
         "--", path],
        capture_output=True, text=True, cwd=_ROOT, check=False,
    )
    values: dict[float, str] = {}
    for sha in log.stdout.split():
        blob = subprocess.run(
            ["git", "show", f"{sha}:{path}"],
            capture_output=True, text=True, cwd=_ROOT, check=False,
        )
        if blob.returncode:
            continue
        try:
            tree = ast.parse(blob.stdout)
        except SyntaxError:
            continue
        found = _module_constants(tree).get(const)
        if found is not None:
            values.setdefault(found[0], sha)
    return values


@dataclass(frozen=True)
class Report:
    """What was looked at, not only what was found."""

    sites: tuple[Site, ...]
    constants: int
    with_unit: int
    with_prose: int
    unreadable: tuple[str, ...]


def _companions(root: pathlib.Path,
                names: frozenset[str]) -> list[tuple[str, str, list[tuple[int, str]]]]:
    """``(relative path, whole text, prose spans)`` for every file under *root*.

    THE THIRD SURFACE. A tunable's rationale is not confined to the module that
    declares it: `db_reclaim.py` stated its retention window in a constant block,
    in a method docstring, AND in the module docstring of
    `tests/scheduler/handlers/test_the_run_history_is_finally_bounded.py`. The
    same-file rule cannot reach the third, because a test declares no constant —
    and that copy was stale in exactly the same way, found by hand rather than by
    this tool.

    PARSED ONLY WHEN IT COULD MATTER. `tests/` is 1,728 files here and parsing
    every one costs more than the git step this tool is built around — and a
    guard that makes the gate slower is a guard that gets skipped, which is the
    failure mode rather than the cost. A file that names none of the modules
    holding a unit-bearing constant can produce no pairing, so it is filtered on
    a raw substring before it is ever parsed.
    """
    out: list[tuple[str, frozenset[str], list[tuple[int, str]]]] = []
    for path in sorted(root.rglob("*.py")):
        text = path.read_text(encoding="utf-8", errors="replace")
        # Cheap gate before the parse: a file naming no candidate module at all
        # cannot pair with one.
        if not any(name in text for name in names):
            continue
        try:
            tree = ast.parse(text)
            spans = prose_spans(text, tree)
        except (SyntaxError, tokenize.TokenError, IndentationError):
            continue
        out.append((str(path.relative_to(_ROOT)), imported_modules(tree), spans))
    return out


def imported_modules(tree: ast.Module) -> frozenset[str]:
    """Dotted names this module imports, including ``from pkg import module``.

    THE PAIRING MUST BE AN IMPORT, NOT A WORD. The first cut paired a companion
    with any constant whose module STEM appeared in its text, and the stems in
    this tree include `base`, `store`, `shared` and `registry` — words that occur
    in ordinary English prose. Every test would have answered to those modules'
    constants. Reading the import table is both exact and cheaper than being
    wrong.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return frozenset(names)


def scan_tree(root: pathlib.Path = _SRC,
              companion_root: pathlib.Path | None = _TESTS) -> Report:
    sites: list[Site] = []
    constants = with_unit = with_prose = 0
    unreadable: list[str] = []
    modules = sorted(root.rglob("*.py"))
    # The names a companion must mention to be worth parsing: the dotted path and
    # the bare stem of every module that declares a constant carrying a unit.
    names: set[str] = set()
    for path in modules:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        if any(UNITS.get(c.rstrip("_").split("_")[-1])
               for c in _module_constants(tree)):
            rel = str(path.relative_to(_ROOT))
            names.add(rel[len("src/"):-len(".py")].replace("/", "."))
            names.add(path.stem)
    companions = (
        _companions(companion_root, frozenset(names))
        if companion_root is not None else []
    )
    for path in modules:
        source = path.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(source)
            spans = prose_spans(source, tree)
        except (SyntaxError, tokenize.TokenError, IndentationError):
            unreadable.append(str(path.relative_to(_ROOT)))
            continue
        rel = str(path.relative_to(_ROOT))
        dotted = rel[len("src/"):-len(".py")].replace("/", ".")
        for const, (value, _lineno) in _module_constants(tree).items():
            constants += 1
            unit = UNITS.get(const.rstrip("_").split("_")[-1])
            if unit is None:
                continue
            with_unit += 1
            # Same file first, then every companion that NAMES this module. The
            # naming filter is what keeps this from pairing each figure with each
            # constant: without it "100 days" anywhere would answer to every
            # constant counted in days.
            candidates: list[tuple[str, dict[float, list[int]]]] = []
            same = stated_values(spans, unit, exclude=value)
            if same:
                candidates.append((rel, same))
            for crel, cimports, cspans in companions:
                if dotted not in cimports:
                    continue
                found = stated_values(cspans, unit, exclude=value)
                if found:
                    candidates.append((crel, found))
            if not candidates:
                continue
            with_prose += 1
            history = past_values(rel, const)
            for where, stated in candidates:
                for number, lines in sorted(stated.items()):
                    sha = history.get(number)
                    if sha is None:
                        continue
                    for line in lines:
                        sites.append(
                            Site(where, const, value, number, unit, line, sha)
                        )
    return Report(tuple(sites), constants, with_unit, with_prose, tuple(unreadable))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verbose", action="store_true",
                        help="print the denominator this result stands on")
    args = parser.parse_args(argv)

    report = scan_tree()
    print("PROSE STATING A VALUE ITS OWN CONSTANT NO LONGER HAS")
    print(f"  {len(report.sites)} site(s), across "
          f"{len({s.const for s in report.sites})} constant(s).")
    print("  A superseded value in prose is EVIDENCE, not a verdict — history "
          "kept on purpose reads the same way to a scanner.")
    print()
    for site in report.sites:
        print(f"{site.path}:{site.lineno}")
        print(f"        {site.const} is now {_fmt(site.current)}; this line says "
              f"{_fmt(site.stated)} {site.unit}")
        print(f"        that value was the constant's own, until {site.sha[:8]}")
        print(f"        key: {site.key}")
    if args.verbose:
        print()
        print(f"  module-level numeric constants  {report.constants}")
        print(f"  ... with a recognised unit      {report.with_unit}")
        print(f"  ... whose prose names another   {report.with_prose}")
        print(f"  unreadable files                {len(report.unreadable)}")
        print("  The middle number is the one that matters: without the git step "
              "this tool would report all of them.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
