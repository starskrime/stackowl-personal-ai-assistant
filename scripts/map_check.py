#!/usr/bin/env python3
"""Does the reference map already have an item for what I am about to build?

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


def terms_from(argv: list[str]) -> list[str]:
    """Search terms from a command line — WORDS, never argv elements.

    A term is a WORD. An argv element may be a whole quoted phrase, and
    `"the entire phrase" in haystack` is essentially never true. MEASURED
    2026-09-11: `map_check.py skill curator decay` returned ten matches and
    `map_check.py "skill curator decay"` returned "The ground is clear" — and the
    quoted form is the one `item-loop/SKILL.md` instructs every loop to use, so
    the tool built to stop work being rebuilt had been answering all-clear to its
    only caller. Its own docstring carried both forms as if they were equivalent.
    """
    return [w for t in argv for w in t.lower().split() if w]


def _discriminating(terms: list[str], items: list[dict]) -> list[str]:
    """Drop the terms that carry no information about WHICH item you want.

    WHY THIS IS DERIVED AND NOT A WORD LIST. Hard-coded stopword lists are banned
    here — the platform is multilingual and a list of English articles is wrong the
    first time someone searches in another language. Inverse document frequency is
    the same idea computed from the corpus, so it holds in any language and adapts as
    the map grows.

    AND THE CUT COMES FROM THE QUERY, NOT FROM A CONSTANT. A term is dropped when its
    IDF is below the MEAN IDF of the query's own matching terms — purely relative, so
    there is no threshold to justify and none to re-tune as the map changes. The
    first attempt used a fixed "more than half the map" and did nothing at all: `the`
    matches 50 of 137 items, which is 36%, comfortably under any such line. A number
    that sounds principled is not one.

    MEASURED 2026-09-11, on a regression I shipped the previous day. Splitting an
    argv element into WORDS fixed a real defect — a quoted phrase had matched
    nothing, ever — and traded it for the opposite one. "warn when the webhook
    receiver is bound to loopback but has configured sources" returned **81 items**
    at a top score of 4, and three of those four hits were `the` (50 items), `is`
    (48) and `to` (44), while the terms carrying the meaning matched almost nothing:
    `webhook` 1, `configured` 1, `loopback` 0. The ranking was noise.

    EVERY DROPPED TERM IS REPORTED by `main`: a search that silently changes the
    question is the instrument error this file exists to prevent.

    THE FILTER CANNOT SILENCE A QUERY, and it needs no guard to say so. The cut is
    the MEAN of the matching terms' IDFs, so at least one term always sits at or
    above it — dropping everything is arithmetically impossible. A defensive
    "if everything was dropped, keep the originals" branch was written here and
    DELETED: mutation testing removed it and every test still passed, which is what
    unreachable code looks like from the outside. The one real silence risk is a
    query of entirely absent words, and that returns nothing for the honest reason
    that nothing matches.
    """
    import math

    if not items or len(terms) < 2:
        return terms
    hits = {t: sum(1 for i in items if _score(i, [t])) for t in terms}
    matching = {t: n for t, n in hits.items() if n}
    if len(matching) < 2:
        return terms
    idf = {t: math.log(len(items) / n) for t, n in matching.items()}
    cut = sum(idf.values()) / len(idf)
    kept = [t for t in terms if t not in matching or idf[t] >= cut]
    dropped = [(t, hits[t] / len(items)) for t in terms if t in matching and idf[t] < cut]
    _discriminating.dropped = dropped  # type: ignore[attr-defined]
    return kept


def search(argv: list[str], data: dict | None = None) -> list[tuple[int, dict]]:
    """(score, item) for every match, best first. THE one search path.

    Exposed rather than inlined into `main` so a test can exercise what the
    command actually does. The first guard written for this reimplemented the
    term-splitting inside the test, so reverting the real thing changed nothing
    the test could see and both mutations passed — a fixture that cannot show the
    bug proves nothing.
    """
    terms = terms_from(argv)
    data = data if data is not None else yaml.safe_load(_PROGRESS.read_text())
    terms = _discriminating(terms, data["items"])
    scored = [(s, i) for i in data["items"] if (s := _score(i, terms))]
    # `wave` may be absent or null — N01 carries `wave: None` and has since it was
    # added, so sorting a mixed result set raised TypeError. That crash was LATENT
    # behind the argv defect above: the documented invocation never matched
    # anything, so this sort never ran on more than zero items. One hid the other.
    scored.sort(key=lambda si: (-si[0], si[1].get("wave") or 99, si[1]["id"]))
    return scored


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 0
    terms = terms_from(argv)
    data = yaml.safe_load(_PROGRESS.read_text())
    scored = search(argv, data)
    if not scored:
        print(f"No mapped item matches {terms}. The ground is clear.")
        return 0

    dropped = getattr(_discriminating, "dropped", [])
    if dropped:
        print(
            "  (ignored, too common in this map to discriminate: "
            + ", ".join(f"{t!r} in {share:.0%} of items" for t, share in dropped)
            + ")"
        )
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
