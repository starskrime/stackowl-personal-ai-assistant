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

AND "STILL PRODUCIBLE" IS NOT "STILL HAPPENING" — the distinction this script
was missing on the day it shipped, which cost the very next loop two more
investigations. `[scheduler] compute_next_run: cron parse failed — defaulting to
+1d` is LIVE by this script's own test (the line is right there in
`scheduler_helpers.py`) and reads as 326 mis-scheduled jobs. Its last occurrence
is 2026-08-31: `scheduler.py` learned to ask `_is_recurring` instead, and its
comment already rejects the tightening the log invites — "manual is not a cadence,
so tightening compute_next_run would only change WHICH wrong cadence a one-shot
gets". Every manual job in the database is `completed`. So the report now carries
LAST SEEN and splits live messages into CURRENT and DORMANT.

THE CUTOFF IS READ FROM THE CORPUS, never chosen: the newest date any record
carries. A message last seen on that date is CURRENT; anything older is DORMANT
and shows its date. A constant here would be a second thing to keep true, and the
logs already know the answer.

WHAT IT DOES NOT CLAIM. A retired message is not a defect and not a cleanup task:
the corpus SHOULD hold what the platform used to say. Nor is a dormant one — it
may be seasonal, or waiting on a condition. Both are caveats for the READER, so a
count from the logs is not mistaken for current behaviour.
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


#: A line every process writes exactly once, at startup.
#:
#: THE DENOMINATOR THIS REPORT WAS MISSING. It is emitted at INFO, so it is not in
#: the WARNING-and-above corpus this report normally reads — it is counted in a
#: separate, level-blind pass for that reason.
_BOOT_MARKER = "[startup] browser_probe.check: exit"


def per_boot(
    paths: list[pathlib.Path], levels: set[str] | None
) -> tuple[int, dict[str, int]]:
    """``(process lifetimes, {message: the MOST it ever fired in one lifetime})``.

    THE RATIO WAS NOT ENOUGH, and this is the measurement that proved it. An
    earlier cut flagged "startup fact" on a rate between 0.8 and 1.2 per boot —
    a magic number, and it MISSED the message that prompted this: the thin-tool
    lint sits at 0.75/boot because it fires in 827 of 1,096 lifetimes and not at
    all in the other 269. Its rate looks unremarkable; its MAXIMUM is 1, and that
    is the fact that settles it.

    A message whose maximum is 1 cannot be a recurring condition, whatever its
    total: the total is then a measure of how often this box RESTARTS. That
    matters here more than anywhere, because MEASURED 2026-09-10 **580 of the
    boots in this corpus are CodeWatcher re-execs** — this programme editing the
    instance it is measuring. Ranking by total therefore promotes startup facts
    in proportion to how much code was edited that week.
    """
    events: list[tuple[str, str | None]] = []
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for line in text.splitlines():
            if not line.startswith('{"ts"'):
                continue
            boot = _BOOT_MARKER in line
            # Cheap reject before the parse: the level check is what makes this
            # affordable over a 15 MB file.
            if (
                not boot
                and levels is not None
                and not any(f'"level": "{lv}"' in line for lv in levels)
            ):
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if not isinstance(rec, dict):
                continue
            ts = rec.get("ts")
            if not isinstance(ts, str):
                continue
            if boot:
                events.append((ts, None))
                continue
            if levels is not None and rec.get("level") not in levels:
                continue
            msg = rec.get("msg")
            if isinstance(msg, str) and msg:
                events.append((ts, msg))
    events.sort()
    boots = 0
    window: dict[str, int] = {}
    maxima: dict[str, int] = {}
    for _ts, msg in events:
        if msg is None:
            for m, n in window.items():
                if n > maxima.get(m, 0):
                    maxima[m] = n
            window = {}
            boots += 1
            continue
        window[msg] = window.get(msg, 0) + 1
    for m, n in window.items():
        if n > maxima.get(m, 0):
            maxima[m] = n
    return boots, maxima


def corpus_messages(
    paths: list[pathlib.Path], levels: set[str] | None
) -> tuple[dict[str, int], dict[str, str]]:
    """``({message: occurrences}, {message: last ts})`` over the given jsonl logs.

    ONE PASS for both. Reading the corpus twice to answer two questions about the
    same records is how a 15 MB-per-file scan becomes the reason nobody runs the
    report.

    Reads with ``errors="ignore"``: these files contain a NUL block that makes
    `/usr/bin/grep` truncate and `jq` complain, and neither is a reason to
    under-count. A line that will not parse is skipped, not fatal.
    """
    counts: dict[str, int] = {}
    last: dict[str, str] = {}
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
                ts = rec.get("ts")
                if isinstance(ts, str) and ts > last.get(msg, ""):
                    last[msg] = ts
    return counts, last


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

    counts, last = corpus_messages(paths, levels)
    gone = retired(counts, static_prefixes())
    scope = "every level" if args.all_levels else "WARNING and above"
    retired_msgs = {m for _n, m in gone}

    # THE CUTOFF IS THE CORPUS'S OWN NEWEST DAY — see the module docstring. With
    # no records at all there is nothing to be current relative to, and every
    # message falls to DORMANT rather than to a guessed date.
    newest_day = max((ts[:10] for ts in last.values()), default="")
    current, dormant = [], []
    for msg, n in counts.items():
        if msg in retired_msgs:
            continue
        (current if last.get(msg, "")[:10] == newest_day else dormant).append(
            (n, last.get(msg, "")[:10], msg)
        )

    print(f"LOG MESSAGE PROVENANCE — {scope}, {len(paths)} file(s), newest day {newest_day}")
    print(f"  {len(counts)} distinct message(s): {len(current)} CURRENT, "
          f"{len(dormant)} DORMANT, {len(gone)} RETIRED.")
    print("  RETIRED — no `log.*` call in src/ can emit this any more. Its occurrences")
    print("            are a record of the PAST; counting them as current behaviour is")
    print("            how a fixed defect gets re-opened.")
    print("  DORMANT — the code CAN still emit it, but nothing has since the date shown.")
    print("            Producible is not happening: the branch may already be unreachable.")
    print()
    boots, maxima = per_boot(paths, levels)
    print(f"CURRENT ({len(current)}) — seen on {newest_day}")
    if boots:
        startup = sum(1 for _n, _d, m in current if maxima.get(m) == 1)
        print(f"  Denominator: {boots} process lifetimes. A message marked STARTUP FACT "
              f"never fired twice in ONE process, so its TOTAL measures how often this box "
              f"RESTARTS, not how often the condition occurs — and on this box most boots "
              f"are CodeWatcher re-execs after a src/ edit. {startup} of {len(current)} "
              f"CURRENT messages are startup facts; rank on the rest.")
    for n, _d, msg in sorted(current, reverse=True):
        rate = f"{n / boots:>6.2f}/boot" if boots else " " * 11
        flag = "  STARTUP FACT (max 1 per process)" if maxima.get(msg) == 1 else ""
        print(f"{n:>7} {rate}  {msg[:88]}{flag}")
    print()
    print(f"DORMANT ({len(dormant)}) — producible, last seen:")
    for n, d, msg in sorted(dormant, key=lambda x: (x[1], -x[0])):
        print(f"{n:>7}  {d}  {msg[:92]}")
    print()
    print(f"RETIRED ({len(gone)})")
    for n, msg in gone:
        print(f"{n:>7}  {msg[:104]}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
