#!/usr/bin/env python3
"""Does every tool that says it STARTED also say how it ENDED?

WHY THIS IS A CORPUS QUESTION AND NOT AN AST ONE, measured the hard way on
2026-09-10. Two static scans were written for this and BOTH were wrong, and both
looked authoritative:

  * the first scoped the search to the FUNCTION holding the entry, and reported all
    66 tools as offenders — because most tools log their exit from a shared helper.
    66 of 66 is the shape of an instrument error, not a finding.
  * the second scoped it to the MODULE and reported five. Four of those five were
    browser tools whose exit IS logged, through a helper that builds the message as
    `f"{tool}.execute: exit"` — a string no AST walk can read.

The CORPUS settled both, instantly and without ambiguity, because it counts what was
actually written. Of ~35 tools with traffic, every one pairs entry to exit within
0.5% — except three, and the exceptions were the finding:

    shell             entry 312   exit   0   (exit was at DEBUG; this box has never
                                              written a DEBUG record — 0 of 677,108)
    browser_eval_js   entry  48   exit   1   (its exits were filed under the helper's
                                              default parameter value)
    web_fetch         entry 814   exit 610   (failing paths return before the exit)

and `browser_tool.execute: exit` appears **1,235 times against ZERO entries**, because
`browser_tool` is not a tool at all — it was `_ok`/`_err`'s default keyword.

So this file carries BOTH halves and reports them separately: the STATIC contract (an
entry logged at INFO+ implies an exit logged at INFO+, with helper-forwarded names
resolved) and the LIVE pairing (counts per tool from the retained logs). The static
half is what a tripwire can ratchet; the live half is what tells you the static half
is asking the right question.
"""

from __future__ import annotations

import ast
import collections
import json
import pathlib
import re

_SRC = pathlib.Path(__file__).resolve().parent.parent / "src" / "stackowl"
_LOUD = {"info", "warning", "error", "critical"}
_MSG = re.compile(r"^([a-z_][a-z0-9_]*)\.execute: (entry|exit)")

#: Helpers that log `f"{tool}.execute: exit"` for a caller-supplied name. A call to
#: one of these with `tool="X"` IS an exit for X, at the helper's own level, and no
#: literal for X exists anywhere. Listing them is not a loophole — it is the only way
#: an AST walk can see through an f-string, and the level is read from the helper.
_FORWARDING_HELPERS = {"_ok", "_err", "_browser_failure"}


def _log_level(node: ast.Call) -> str | None:
    """`log.tool.info(...)` -> "info". None when this is not a logging call."""
    if not (isinstance(node.func, ast.Attribute) and node.args):
        return None
    return node.func.attr


def entry_exit_levels(root: pathlib.Path | None = None) -> dict[str, dict[str, object]]:
    """Per tool: the level its entry is logged at, and every level an exit is."""
    src = root or _SRC
    entries: dict[str, tuple[str, str, int]] = {}
    exits: dict[str, set[str]] = collections.defaultdict(set)
    forwarded: dict[str, set[str]] = collections.defaultdict(set)
    helper_levels: dict[str, str] = {}

    for path in sorted(src.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        # What level does each forwarding helper log its exit at?
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if fn.name not in _FORWARDING_HELPERS:
                continue
            for n in ast.walk(fn):
                if not (isinstance(n, ast.Call) and n.args):
                    continue
                lvl = _log_level(n)
                a = n.args[0]
                if lvl and isinstance(a, ast.JoinedStr):
                    flat = "".join(
                        p.value for p in a.values
                        if isinstance(p, ast.Constant) and isinstance(p.value, str)
                    )
                    if ".execute: exit" in flat:
                        helper_levels[fn.name] = lvl
        for n in ast.walk(tree):
            if not isinstance(n, ast.Call):
                continue
            # A literal `X.execute: entry` / `X.execute: exit`.
            lvl = _log_level(n)
            if lvl and n.args:
                a = n.args[0]
                if isinstance(a, ast.Constant) and isinstance(a.value, str):
                    m = _MSG.match(a.value)
                    if m:
                        if m.group(2) == "entry":
                            entries[m.group(1)] = (lvl, str(path), n.lineno)
                        else:
                            exits[m.group(1)].add(lvl)
            # A call INTO a forwarding helper carries the name as a keyword.
            if isinstance(n.func, ast.Name) and n.func.id in _FORWARDING_HELPERS:
                for kw in n.keywords:
                    if kw.arg == "tool" and isinstance(kw.value, ast.Constant):
                        forwarded[str(kw.value.value)].add(n.func.id)

    out: dict[str, dict[str, object]] = {}
    for tool, (lvl, path, lineno) in sorted(entries.items()):
        levels = set(exits.get(tool, set()))
        for helper in forwarded.get(tool, set()):
            if helper in helper_levels:
                levels.add(helper_levels[helper])
        out[tool] = {"entry": lvl, "exits": sorted(levels), "file": path, "line": lineno}
    return out


def unpaired(root: pathlib.Path | None = None) -> list[str]:
    """Tools whose entry is loud and whose exit is not — the checkable contract."""
    bad = []
    for tool, info in entry_exit_levels(root).items():
        if info["entry"] not in _LOUD:
            continue
        if not (set(info["exits"]) & _LOUD):  # type: ignore[arg-type]
            bad.append(tool)
    return sorted(bad)


def corpus_pairs(log_dir: pathlib.Path) -> dict[str, tuple[int, int]]:
    """Live entry/exit counts per tool across every retained log."""
    counts: dict[str, list[int]] = collections.defaultdict(lambda: [0, 0])
    for path in sorted(log_dir.glob("stackowl*.jsonl")):
        with path.open("rb") as fh:
            for raw in fh:
                try:
                    rec = json.loads(raw)
                except (ValueError, UnicodeDecodeError):
                    continue
                m = _MSG.match(str(rec.get("msg", "")))
                if m:
                    counts[m.group(1)][0 if m.group(2) == "entry" else 1] += 1
    return {k: (v[0], v[1]) for k, v in counts.items()}


def main() -> None:
    static = entry_exit_levels()
    loud = [t for t, i in static.items() if i["entry"] in _LOUD]
    bad = unpaired()
    print(f"STATIC CONTRACT — {len(static)} tools log an execute: entry, {len(loud)} at INFO+")
    if bad:
        print(f"  ENTRY LOUD, EXIT NOT ({len(bad)}):")
        for t in bad:
            i = static[t]
            print(f"    {t:<22} entry={i['entry']} exits={','.join(i['exits']) or 'NONE'}  {i['file']}:{i['line']}")
    else:
        print("  every loud entry has a loud exit.")
    quiet = sorted(t for t, i in static.items() if i["entry"] not in _LOUD)
    print(f"\n  ENTRY ITSELF AT DEBUG ({len(quiet)}) — invisible in production either way,")
    print("  but consistently so, which is a different question from an ASYMMETRIC pair:")
    print(f"    {', '.join(quiet)}")

    from stackowl.paths import StackowlHome

    pairs = corpus_pairs(pathlib.Path(StackowlHome.logs_dir()))
    print(f"\nLIVE PAIRING — {len(pairs)} tool name(s) appear in the retained logs")
    rows = sorted(pairs.items(), key=lambda kv: kv[1][0] - kv[1][1], reverse=True)
    for tool, (e, x) in rows:
        gap = e - x
        if gap or e == 0:
            print(f"    {tool:<22} entry={e:<6} exit={x:<6} gap={gap}")
    print("  (an entry of 0 with a non-zero exit means the exit is filed under a name")
    print("   nothing enters by — a helper default, not a tool.)")


if __name__ == "__main__":
    main()
