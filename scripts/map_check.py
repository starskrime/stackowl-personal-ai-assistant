#!/usr/bin/env python3
"""Does the Hermes map already have an item for what I am about to build?

WHY THIS EXISTS. On 2026-08-07 the skill-lifecycle curator was designed,
implemented, tested, validated and shipped — and only afterwards found to be
``D09.3 "Skill curator"``, wave 4, priority P1, whose stated gap was almost
word for word the problem being solved: *"Skills accumulate and rot with no
pressure to consolidate."* ``progress.yml`` recorded it untouched and wave 4 as
0/12 the whole time.

Nothing was wrong with the work. What was wrong is that PROCESS.md's rule —
*"before writing anything, check dedup_targets"* — is a rule someone has to
remember, and under evidence-first working, where priority comes from live
measurements rather than from reading the map, remembering is exactly what
fails. So the check becomes a command.

USAGE

    uv run python scripts/map_check.py skill curator decay
    uv run python scripts/map_check.py "background review fork"

Prints every item whose title or gap matches, with the three things that change
what you do next: whether it is already claimed, what it DEPENDS ON, and its
``dedup_target`` (which PROCESS.md says is part of the item, not follow-up).

Exit status is 1 when something matched — so it can gate a workflow — and 0 when
the ground is genuinely clear.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

_STAGES = (
    "brainstorm", "architect", "implement",
    "cleanup", "test", "validate", "document",
)
_PROGRESS = Path(__file__).resolve().parent.parent / "progress.yml"


def _state(item: dict) -> str:
    stages = item.get("stages") or {}
    values = [stages.get(k) for k in _STAGES]
    if all(v in ("done", "no_change_needed") for v in values):
        return "COMPLETE"
    if any(v not in (None, "not_started") for v in values):
        return "PARTIAL"
    return "untouched"


def _score(item: dict, terms: list[str]) -> int:
    """How many search terms appear in the item's title or stated gap.

    Title and gap only — deliberately. Matching the whole record would hit the
    prose in `notes` and `changes` and return half the map for any common word,
    which is the same as returning nothing.
    """
    hay = f"{item.get('title', '')} {item.get('gap', '')}".lower()
    return sum(1 for t in terms if t in hay)


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 0
    terms = [t.lower() for t in argv if t.strip()]
    data = yaml.safe_load(_PROGRESS.read_text())

    scored = [
        (s, i) for i in data["items"]
        if (s := _score(i, terms))
    ]
    if not scored:
        print(f"No mapped item matches {terms}. The ground is clear.")
        return 0

    scored.sort(key=lambda si: (-si[0], si[1]["wave"], si[1]["id"]))
    print(f"{len(scored)} mapped item(s) match {terms} — read before building:\n")
    for score, item in scored[:10]:
        state = _state(item)
        print(f"  {item['id']}  wave {item['wave']}  {item.get('priority', 'P?')}  [{state}]  "
              f"hits={score}")
        print(f"      {item['title']}")
        if item.get("gap"):
            print(f"      gap: {item['gap']}")
        # The two fields that most often turn a "quick fix" into a bigger item.
        if item.get("depends_on"):
            print(f"      DEPENDS ON: {item['depends_on']}  (build these first, or say why not)")
        if item.get("dedup_target"):
            tgt = item["dedup_target"]
            dedup = (data.get("dedup_targets") or {}).get(tgt, {})
            print(f"      DEDUP {tgt}: {dedup.get('detail', '(see progress.yml)')}")
            print(f"            status: {dedup.get('status', 'unknown')} "
                  f"— PROCESS.md: resolving this is PART of the item")
        if item.get("doc"):
            print(f"      doc: {item['doc']}")
        print()
    print("If you build any of these, run its seven stages and update progress.yml.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
