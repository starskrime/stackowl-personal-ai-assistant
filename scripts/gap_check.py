#!/usr/bin/env python3
"""Run every item's `gap_check` and say which gap statements reality has refuted.

THE FOURTH INSTANCE OF ONE CURE, and the one that decides what gets BUILT.
An escalation's premise aged silently until `premise_check`. A partial stage's
evidence aged silently until `closing_check`. A document's claim aged silently
until `doc_check`. An item's **gap** — the sentence that says what is missing,
and therefore what the next loop constructs — was checked by nothing at all.

MEASURED 2026-09-11, against the 25-item Agentic OS series written in one sitting
from the operator's own phrasing: **17 gaps assert an ABSENCE** ("cannot", "no
way", "invisible", "never built", "not visible anywhere"). NINE were examined
against the tree and **SEVEN were wrong or overstated**. Two held as written.
The four that decided the most:

  * A05.2 said "the customer cannot change a setting without editing YAML".
    `/config set` exists, validates, writes, and RE-READS the file to confirm the
    write persisted (F-81).
  * A05.4 said cron jobs were "invisible unless the operator queries the database
    by hand". `tools/scheduling/cronjob.py` declares EIGHT actions — create,
    watch, list, update, pause, resume, remove, run. (Corrected earlier.)
  * A01.4 said stop conditions "were never built". ESC-170, in the SAME FILE,
    records "the budget stop is already on and firing — 101 stops, 19 on
    2026-09-10". The record contradicted itself, both halves written the same day.

AND THE SHAPE OF THE ERROR IS CONSISTENT, which is what makes it worth a script
rather than three corrections. Each gap says THE CAPABILITY DOES NOT EXIST when
the truth is THE CAPABILITY IS NOT REACHABLE AS A SET, OR FROM A BROWSER. Those
are different items: the first rebuilds a working tool, the second presents one.
A05.4's correction had already made exactly that move, and nothing generalised it.

  * A04.1 said "there is no single descriptor an agent can be listed from, routed
    by, or reported on", on an evidence line reading "zero files matching
    AgentCard or agent_card in src/" — A GREP FOR A NAME THAT WAS COMPOSED, not
    found. `OwlAgentManifest` carries 23 fields and is read by 29 modules, and
    migration 0118 consolidated an owl's four homes a month earlier. A04.1 is P1
    and blocks two items: built as written it would have minted a SECOND
    descriptor beside the shared one.
  * A04.2 said "no pool, no lease, no activation record". The lease EXISTS —
    `tasks.lease_owner` (0053) and `lease_expires_at` (0119), CAS claim and crash
    reclaim. It is on the WORK, not the agent. Left standing, a gap that says "no
    lease" instructs the next loop to build a second one, against the operator's
    standing rule that no implementation may duplicate logic that already runs
    work.
  * A05.5 and A05.6 were OVERSTATED in the same direction: `/memory search` and
    `task_status` both work, per row. `task_status` REQUIRES an exact `task_id`,
    and there is no task list at all — so the truth is "not reachable as a set",
    which is a different and much smaller item than "nothing shows work".

NOT A GATE, for the reason `doc_check` is not one: 8 absence-claiming gaps still
carry no check, and a guard failing every one of them would be bypassed rather
than satisfied. The count is what makes the class drainable.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parent.parent

#: Words that turn a gap from a description into a CLAIM ABOUT ABSENCE — the kind
#: reality can refute. A gap saying "the dashboard should show X" asserts nothing
#: checkable; "X is not visible anywhere" does.
_ABSENCE = (
    "cannot", "can only", "no way", "invisible", "not visible", "nothing",
    "never", "only by", "there is no", "has no", "no single", "not built",
)


def _items() -> list[dict]:
    data = yaml.safe_load((_ROOT / "progress.yml").read_text(encoding="utf-8"))
    return [i for i in data.get("items", []) if isinstance(i, dict)]


def _asserts_absence(gap: str) -> bool:
    low = gap.casefold()
    return any(w in low for w in _ABSENCE)


def _is_complete(item: dict) -> bool:
    """A gap decides what gets BUILT, so a built item's gap has nothing left to decide.

    MEASURED 2026-09-11: of 20 absence-claiming gaps in the whole record, SEVEN
    belong to items every stage of which is `done` or `no_change_needed` — D01.6,
    D01.7, D04.4, D05.2, D10.6, D18.3 and A05.1, whose control plane shipped
    hours before this script was written. Their gaps read "there is no surface
    through which a customer can see or steer the platform" and reality has
    refuted every one of them, WHICH IS WHAT SUCCESS LOOKS LIKE. Reporting seven
    permanent entries beside thirteen actionable ones is how a report stops being
    read.

    They are counted and named in the summary rather than silently dropped: a
    filtered denominator nobody can see is the error this programme pays for most.
    """
    stages = item.get("stages")
    if not isinstance(stages, dict) or not stages:
        return False
    return all(v in ("done", "no_change_needed") for v in stages.values())


def main() -> int:
    items = _items()
    checked = refuted = 0
    unchecked: list[str] = []
    historical: list[str] = []

    for item in items:
        gap = str(item.get("gap") or "")
        if not gap:
            continue
        check = item.get("gap_check")
        if not check:
            if _asserts_absence(gap):
                (historical if _is_complete(item) else unchecked).append(
                    f"{item['id']}: {gap[:96]}"
                )
            continue
        checked += 1
        out = subprocess.run(
            ["bash", "-c", str(check)], cwd=_ROOT, capture_output=True, text=True
        ).stdout.strip()
        line = out.splitlines()[-1] if out else "(no output)"
        if line.startswith("REFUTED"):
            refuted += 1
            print(f"  REFUTED  {item['id']}\n           {line}")
        else:
            print(f"  holds    {item['id']}  [{line[:88]}]")

    print()
    print(f"checked {checked}, REFUTED {refuted}, "
          f"absence-claiming gaps with no gap_check {len(unchecked)} "
          f"(+{len(historical)} on COMPLETE items, historical: "
          f"{', '.join(h.split(':')[0] for h in historical)})")
    if unchecked:
        print()
        print("A GAP NOTHING CHECKS IS THE ONE CLAIM THAT DECIDES WHAT GETS BUILT.")
        print("SEVEN of the first NINE examined were wrong; these are unexamined:")
        for u in unchecked:
            print(f"  {u}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
