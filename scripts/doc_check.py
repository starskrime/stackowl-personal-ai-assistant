#!/usr/bin/env python3
"""Which design documents have gone stale against the code they describe?

WHY THIS EXISTS. `DOC_STANDARD` requires every design document to carry
``Last verified: YYYY-MM-DD, against commit <sha>`` and a ``Source:`` naming the
files it describes. That pairing makes staleness a CHECKABLE property — and
nothing ever checked it, so the claim aged exactly the way an escalation's
premise aged before `premise_check`, and a `partial` stage's evidence aged before
`closing_check`. Both were cured by making the claim executable. This is the same
cure for the third instance.

MEASURED 2026-09-06 over 84 documents: **15 are stale** — their cited sources
were last changed AFTER the date the document says it was verified, D01.1 and
D01.7 by six weeks. Nothing anywhere said so.

IT REPORTS TWO POPULATIONS AND NEVER CONFLATES THEM, which is the lesson from
the cadence sweep naming its empty stores: a count of "stale" is worthless if 49
documents were silently unmeasurable. A document with no parseable ``Source`` is
UNMEASURABLE, not fresh and not stale, and it is named so the next reader can
see what the number is made of.

NOT A GATE, DELIBERATELY. 15 of 33 measurable documents are stale today; a
tripwire would fail the gate for every unrelated change until someone re-read
fifteen documents, which is how a gate gets bypassed rather than satisfied. It
runs beside `escalation_check.py` and `validate_check.py` at the start of a loop,
where a human decides what to re-verify.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_DESIGNS = _ROOT / "docs" / "reference-mapping" / "designs"

#: The blockquote header DOC_STANDARD prescribes: consecutive `> **Field:** value`
#: lines. Parsed structurally rather than by searching the whole file — a
#: whole-text search for "Last verified" finds it in prose too, and reported 83 of
#: 84 documents compliant when only 35 carry the actual header.
#: The field NAME may carry a qualifier — twelve documents write `Source (new):`,
#: `Source (changed):`, `Source (to change):`, `Source (subject)`. The first
#: version of this pattern was `[A-Za-z ]+`, so a parenthesis made the whole line
#: invisible and D05.1 — which carries a good `Last verified` AND two Source
#: fields — was filed as having neither.
_FIELD = re.compile(r">\s*\*\*([A-Za-z ()]+):\*\*\s*(.*)")


def _header(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        m = _FIELD.match(line.strip())
        if m:
            out[m.group(1).strip()] = m.group(2).strip()
        elif out and not line.strip().startswith(">"):
            break
    return out


def _source_fields(head: dict[str, str]) -> str:
    """Every `Source*` field joined — not just the one spelled exactly `Source`.

    A document that splits its sources across `(new)` and `(changed)` is
    describing both, so dating it by one half would be arbitrary.
    """
    return " ".join(v for k, v in head.items() if k.startswith("Source"))


def _resolve(path: str) -> str | None:
    """A cited path, tried at the repo root and then under `src/stackowl`.

    The variant documents cite `tools/registry.py` and
    `providers/_resilient_round.py` — relative to the package, not the repo — and
    a root-only resolver reported every one of them as non-existent, which is why
    they could not be dated by any path they named.
    """
    for cand in (_ROOT / path, _ROOT / "src" / "stackowl" / path):
        if cand.exists():
            return str(cand.relative_to(_ROOT))
    return None


def _sources_last_changed(paths: list[str]) -> str:
    """The commit date of the most recent change to any cited path (YYYY-MM-DD)."""
    try:
        return subprocess.run(
            ["git", "log", "-1", "--format=%cs", "--", *paths],
            capture_output=True, text=True, timeout=60, cwd=_ROOT,
        ).stdout.strip()
    except Exception:  # pragma: no cover — git absent or path unreadable
        return ""


def main() -> int:
    docs = sorted(_DESIGNS.glob("*.md"))
    stale: list[tuple[str, str, str]] = []
    fresh = 0
    unmeasurable: list[tuple[str, str]] = []

    for doc in docs:
        head = _header(doc.read_text(encoding="utf-8"))
        verified = head.get("Last verified", "")
        source = _source_fields(head)
        date = re.search(r"(\d{4}-\d{2}-\d{2})", verified)
        # Only paths that EXIST — a document citing a moved file cannot be dated
        # by it, and guessing would be worse than saying so.
        cited = [p for p in re.findall(r"`([A-Za-z0-9_./-]+)`", source) if "/" in p]
        real = [r for p in cited if (r := _resolve(p))]
        if not date:
            unmeasurable.append((doc.name, "no dated `Last verified` in the header"))
            continue
        if not real:
            why = "no `Source` in the header" if not source else "no cited path still exists"
            unmeasurable.append((doc.name, why))
            continue
        changed = _sources_last_changed(real)
        if changed and changed > date.group(1):
            stale.append((doc.name, date.group(1), changed))
        else:
            fresh += 1

    print(f"design documents: {len(docs)}\n")
    if stale:
        print(f"STALE — sources changed after the document was verified ({len(stale)}):")
        for name, verified, changed in sorted(stale, key=lambda r: r[1]):
            print(f"  {name:16} verified {verified}   sources changed {changed}")
        print()
    print(f"checked {fresh + len(stale)}, STALE {len(stale)}, "
          f"unmeasurable {len(unmeasurable)}")
    if unmeasurable:
        print("\nUNMEASURABLE — not fresh and not stale; nothing can date them:")
        for name, why in sorted(unmeasurable):
            print(f"  {name:16} {why}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
