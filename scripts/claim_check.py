#!/usr/bin/env python3
"""Which measured claims in progress.yml can still be re-run?

WHY THIS EXISTS. On 2026-08-30 nine map items that rest a ``no_change_needed`` on
a measured zero were re-measured. EIGHT OF THE NINE had decayed, each differently:

    D03.2  "ZERO calls over 50% of the window" was 7; max 23.9% -> 62.1%
    D05.4  "0 tools array CHANGED warnings" was 7, beside 259 prefix invalidations
    D02.1  "ZERO cross-step imports" was 1
    D02.5  "no veto lines" could never have been falsified — both abstaining exits
           logged nothing, so the denominator was invisible
    D01.6  0 cached tokens was TRUE but unreadable: indistinguishable from a
           backend that reports nothing
    D11.2  the claim held, but its FIX landed 69 minutes after the last call that
           could have exercised it, so it has never run in production
    D09.5  its own falsifier fired, and was under-specified: it treated "same
           reason string" as "same root cause", and there were two
    D04.1  satisfied, but only by work done that same night
    D16.3  sound — one of nine

None of that was caused by bad decisions. The decisions were mostly right. They
were UNCHECKABLE, and so they aged without anyone noticing.

``progress_lint`` already states the pattern for writes: "the write happens, the
effect does not, and nothing says so. So it becomes a check." This is the same
sentence about measurements: THE MEASUREMENT HAPPENS, THE CLAIM OUTLIVES IT, AND
NOTHING SAYS SO.

WHAT IT REPORTS. A measured absence — a ZERO, a "never fired", a "must stay at 0"
— is not a fact. It is a fact WITH A DATE. It stays true only while the condition
that produced it holds, so an item closed on one needs a recorded way to re-run
it. This lists the items that carry such a claim and have no closing query, so the
programme can see its own exposure instead of rediscovering it by accident.

WHAT IT DELIBERATELY DOES NOT DO. It does not claim those items are WRONG — there
is no evidence for that, and asserting it would be the same sin in the other
direction. It says only that they cannot be re-checked as written. It is also not
a gate: it always exits 0. Making it fail the build is a process change with a
cost to the operator's workflow, and that is their call, not this script's.

USAGE

    uv run python scripts/claim_check.py            # the risk set
    uv run python scripts/claim_check.py --all      # every item, with its state

Exit status is always 0. This is a report, not a verdict.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

#: The shapes a measured-absence claim takes in this file. Deliberately narrow:
#: "zero" appears in ordinary prose constantly, and a check that flagged every
#: occurrence would be noise rather than signal — the same reason the tool_names
#: rule had to be downgraded.
_ABSENCE = re.compile(
    r"\bZERO\b|zero (?:calls|rows|events|hits|vetoes|implementations)"
    r"|never fired|never fires|must stay at 0|\b0 of \d",
    re.IGNORECASE,
)

#: A recorded way to re-run the claim. Both spellings are in use.
_CLOSING = re.compile(r"CLOSING[_ ]QUERY|closing query", re.IGNORECASE)

#: Only MAP ITEMS are judged. Escalations are questions for the operator, and
#: their prose mentions zeros incidentally — counting those inflated the first
#: measurement of this from 16 to 69 and would have made the report useless.
_MAP_ITEM = re.compile(r"^D\d+\.\d+$")

_STAGES = ("brainstorm", "architect", "implement", "cleanup", "test", "validate", "document")


def _items(text: str) -> list[tuple[str, str]]:
    return re.findall(r"^  - id: (\S+)\n(.*?)(?=^  - id: |\Z)", text, re.M | re.S)


def _closed_stages(body: str) -> tuple[int, int]:
    block = re.search(r"^    stages:\n((?:      \w+:.*\n)+)", body, re.M)
    if not block:
        return (0, 0)
    st = dict(re.findall(r"      (\w+):\s*(\S+)", block.group(1)))
    done = sum(1 for k in _STAGES if st.get(k) in ("done", "no_change_needed"))
    return (done, len(_STAGES))


def main() -> int:
    show_all = "--all" in sys.argv
    path = Path(__file__).resolve().parents[1] / "progress.yml"
    text = path.read_text()

    risk: list[tuple[str, int]] = []
    checkable: list[str] = []
    open_items: list[str] = []

    for iid, body in _items(text):
        if not _MAP_ITEM.match(iid):
            continue
        claims = len(_ABSENCE.findall(body))
        if not claims:
            continue
        done, total = _closed_stages(body)
        if _CLOSING.search(body):
            checkable.append(iid)
        elif done == total and total:
            risk.append((iid, claims))
        else:
            open_items.append(iid)

    print(f"progress.yml — measured-absence claims on map items\n{'=' * 52}")
    print(f"  re-runnable (a closing query is recorded) : {len(checkable)}")
    print(f"  still open (not yet closed on the claim)  : {len(open_items)}")
    print(f"  CLOSED with no way to re-run              : {len(risk)}")

    if risk:
        print("\nclosed on a claim that cannot be re-checked as written:")
        for iid, n in sorted(risk, key=lambda r: -r[1]):
            print(f"   {iid:8s}  {n:2d} absence-claim(s)")
        print(
            "\n  These are not known to be wrong — only unre-runnable. Eight of the\n"
            "  nine audited on 2026-08-30 had decayed, so the base rate is not low.\n"
            "  Adding a CLOSING QUERY to an item moves it out of this list."
        )
    if show_all and checkable:
        print("\nre-runnable:")
        for iid in sorted(checkable):
            print(f"   {iid}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
