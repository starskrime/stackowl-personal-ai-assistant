#!/usr/bin/env python3
"""Which logged messages the code can NO LONGER produce — their hits are history.

WHY THIS EXISTS, and it is the loop that built it. Chasing live signals on
2026-09-09 I opened three investigations off the log and every one was already
answered: `[pipeline] deliver: no registry in services — discarding responses`
reads as 153 discarded answers across 12 days and is the OLD WORDING of a line
`4f3caf19` had already split (the replay lane now logs INFO by design, and the
WARNING that means a real loss has fired ZERO times). The retained corpus keeps
every message a deleted line ever wrote, and nothing marks it deleted, so a grep
returns a confident count for behaviour that cannot happen again.

THE ONLY SIGNAL IS THAT THE STRING IS GONE FROM `src/`, and you have to know to
look. This makes that mechanical: a message no current `log.*` call can emit is
RETIRED, and its occurrences are a record of the past rather than a report about
the present.

    uv run python scripts/retired_log_messages.py            # WARNING and above
    uv run python scripts/retired_log_messages.py --all-levels

THE MATCH HAS TO SURVIVE FORMATTING, and getting that wrong is how this script's
own number moved twice before it was right. A first pass compared the logged
message against whole source literals and called 120 of 328 retired; that counts
every f-string as retired, because the rendered text contains substituted values
the literal never had. Restricting to the literal PARTS of a `JoinedStr` fixed
the f-strings and still said 120 — because %-STYLE calls
(`log.warning("[startup] gateway: browser runtime skipped — %s", reason)`) carry
the placeholder inside the literal, so the literal is not a substring of the
rendered line either. Truncating each fragment at its first placeholder gives
**37 of 328**, and the two lines that exposed each error — a browser-probe line
that had fired minutes earlier — are correctly LIVE. Three classifiers, and only
the third could tell a deleted message from a formatted one.

WHAT IT DOES NOT CLAIM. A retired message is not a defect and not a cleanup task:
the corpus SHOULD hold what the platform used to say. It is a caveat for the
reader, so a count from the logs is not mistaken for current behaviour.
"""

from __future__ import annotations

import argparse
import ast
import json
import pathlib
import re
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src" / "stackowl"

#: Levels production actually writes — see `scripts/logging_visibility.py`, which
#: measured that this deployment has never written a DEBUG record.
PRODUCTION_LEVELS: frozenset[str] = frozenset(
    {"info", "warning", "error", "critical", "exception"}
)

#: Shorter than this and a fragment matches by accident rather than by identity.
_MIN_FRAGMENT = 12

#: %-style placeholders and the start of a mapping key. An f-string's substituted
#: parts are already excluded by taking only the Constant pieces of a JoinedStr.
_PLACEHOLDER = re.compile(r"%[sdrifgxo]|%\(")


def logged_literals(root: pathlib.Path | None = None) -> tuple[tuple[str, str], ...]:
    """Every ``(literal, level)`` a ``log.*`` call in *root* passes as its message.

    ONE SOURCE. `tests/audit/test_a_closing_check_looks_for_evidence_that_can_exist.py`
    asks this rather than keeping its own walk — it needs the same question
    ("can the code emit this string at all?") and two copies of that answer is
    the shape this repo pays for most.

    f-strings contribute their CONSTANT parts only: the substituted parts are
    values at the call site and an AST walk cannot know them.
    """
    src = root if root is not None else _SRC
    out: list[tuple[str, str]] = []
    for path in sorted(src.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if node.func.attr not in PRODUCTION_LEVELS | {"debug"} or not node.args:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                out.append((first.value, node.func.attr))
            elif isinstance(first, ast.JoinedStr):
                for part in first.values:
                    if isinstance(part, ast.Constant) and isinstance(part.value, str):
                        out.append((part.value, node.func.attr))
    return tuple(out)


def static_prefixes(root: pathlib.Path | None = None) -> set[str]:
    """The literal run a RENDERED message must still contain, per emitted fragment."""
    out: set[str] = set()
    for fragment, _level in logged_literals(root):
        head = _PLACEHOLDER.split(fragment)[0].strip()
        if len(head) >= _MIN_FRAGMENT:
            out.add(head)
    return out


def corpus_messages(paths: list[pathlib.Path], levels: set[str] | None) -> dict[str, int]:
    """``{message: occurrences}`` over the given jsonl logs.

    Reads with ``errors="ignore"``: these files contain a NUL block that makes
    `/usr/bin/grep` truncate and `jq` complain, and neither is a reason to
    under-count. A line that will not parse is skipped, not fatal.
    """
    counts: dict[str, int] = {}
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for line in text.splitlines():
            if not line.startswith('{"ts"'):
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if not isinstance(rec, dict):
                continue
            if levels is not None and rec.get("level") not in levels:
                continue
            msg = rec.get("msg")
            if isinstance(msg, str) and msg:
                counts[msg] = counts.get(msg, 0) + 1
    return counts


def retired(counts: dict[str, int], prefixes: set[str]) -> list[tuple[int, str]]:
    """Messages no emitted prefix can account for, commonest first."""
    out = [(n, m) for m, n in counts.items() if not any(p in m for p in prefixes)]
    return sorted(out, reverse=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all-levels", action="store_true",
                        help="include INFO, not just WARNING and above")
    parser.add_argument("--logs", default=str(pathlib.Path.home() / ".stackowl" / "logs"))
    args = parser.parse_args(argv)

    levels = None if args.all_levels else {"WARNING", "ERROR", "CRITICAL"}
    paths = sorted(pathlib.Path(args.logs).glob("stackowl*.jsonl"))
    if not paths:
        print(f"no logs under {args.logs}")
        return 0

    counts = corpus_messages(paths, levels)
    gone = retired(counts, static_prefixes())
    scope = "every level" if args.all_levels else "WARNING and above"

    print(f"RETIRED LOG MESSAGES — {scope}, {len(paths)} file(s)")
    print(f"  {len(counts)} distinct message(s); {len(counts) - len(gone)} still "
          f"producible by src/, {len(gone)} RETIRED.")
    print("  A retired message's occurrences are a record of the PAST. Counting them")
    print("  as current behaviour is how a fixed defect gets re-opened.")
    print()
    for n, msg in gone:
        print(f"{n:>7}  {msg[:112]}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
