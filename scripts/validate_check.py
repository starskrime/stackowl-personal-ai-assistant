#!/usr/bin/env python3
"""Re-run every partial stage's closing check and say which ones can now close.

WHY THIS EXISTS. `escalation_check.py` was written because "an escalation got written
once, with a measurement, and was never looked at again — so its premise aged silently
while it sat in a queue". Every escalation therefore carries a `premise_check` printing
HOLDS or EXPIRED, and the loop runs them all on the way in.

VALIDATE STAGES GOT THE SAME DISCIPLINE IN PROSE AND NEVER IN CODE. `item-loop/SKILL.md`
states the rule — "a check you could not evidence stays OPEN with the query that would
close it" — and MEASURED 2026-09-06 it had not held: ten stages were `partial`, all of
them `validate`, and NOT ONE carried a runnable field. Three had a closing query written
in English inside `changes:`, where nothing could execute it. The other seven had nothing
at all, which made them dead ends rather than open questions: no later pass could close
them, because no one had recorded what closing would look like.

The consequence was quiet and expensive. Ten items sat at 6/7 indefinitely, and the only
thing that could ever advance them was a person remembering, unprompted, to re-derive a
query that mostly did not exist. That is the same "write with no reader" this programme
keeps finding, sitting in its own state of record.

D11.3 is the sharpest case and the reason `progress_lint` now REFUSES a partial without a
check. Its evidence was a frame rendered as text back to the model, logged nowhere at all,
so no volume of production traffic could ever have closed it — D08.1's DEBUG-only evidence
line one step worse. Being forced to write the check is what exposed it; an INFO line was
added in the same change, and only then did the claim become one that reality could settle.

Usage:  uv run python scripts/validate_check.py
Exit status is always 0 — this reports, it does not gate. `progress_lint.py` is the gate,
and it enforces only that a check EXISTS, never what it returns.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "scripts"))
from progress_lint import prose_closing_promises  # noqa: E402
_STAGES = (
    "brainstorm", "architect", "implement", "cleanup", "test", "validate", "document",
)


def main() -> int:
    data = yaml.safe_load((_ROOT / "progress.yml").read_text(encoding="utf-8"))
    items = [
        item for item in data.get("items", [])
        if any((item.get("stages") or {}).get(s) == "partial" for s in _STAGES)
    ]
    # Evidence-led work lives in `known_debt`, not in `items`, so its claims used to
    # have no re-runnable check at all — the dead end DEBT-124 removed for items,
    # one population over. A debt carrying a `closing_check` is now re-run beside
    # them, labelled by its own id.
    items += [
        dict(d, stages={"validate": "partial"})
        for d in (data.get("known_debt", []) or [])
        if (d.get("closing_check") or "").strip()
    ]

    print(f"partial stages: {len(items)} item(s)\n")
    closeable: list[str] = []
    unverifiable: list[str] = []

    for item in sorted(items, key=lambda i: str(i.get("id"))):
        ident = str(item.get("id"))
        stages = [s for s in _STAGES if (item.get("stages") or {}).get(s) == "partial"]
        check = (item.get("closing_check") or "").strip()
        if not check:
            # progress_lint refuses this, so it can only appear on an unlinted edit.
            unverifiable.append(ident)
            print(f"  ????     {ident} ({', '.join(stages)}) — NO closing_check")
            continue
        try:
            out = subprocess.run(
                check, shell=True, cwd=_ROOT, capture_output=True,
                text=True, timeout=180,
            )
            verdict = (out.stdout or out.stderr or "").strip().splitlines()
            line = verdict[-1] if verdict else "<no output>"
        except subprocess.TimeoutExpired:
            line = "<check timed out>"
        except Exception as exc:  # noqa: BLE001 — the message is the output
            line = f"<check raised: {exc}>"

        # The check names its own verdict; this never infers one from an exit code.
        # A check that says nothing is reported as saying nothing, because a silent
        # instrument reading as "still open" is how the prose versions of these
        # queries went unread for a month.
        if line.startswith("CLOSEABLE"):
            closeable.append(ident)
            mark = "CLOSEABLE"
        elif line.startswith("OPEN"):
            mark = "open     "
        else:
            unverifiable.append(ident)
            mark = "?????    "
        print(f"  {mark} {ident} ({', '.join(stages)}) — {line[:150]}")

    print(
        f"\nchecked {len(items)}, CLOSEABLE {len(closeable)}, "
        f"unverifiable {len(unverifiable)}"
    )
    _report_prose_promises(data)
    if closeable:
        print(
            "\nThese have the evidence they were waiting for. Re-read the item, confirm "
            "the check measures what the stage actually claimed, and advance it:\n  "
            + "\n  ".join(closeable)
        )
    return 0


def _report_prose_promises(data: dict) -> None:
    """Name every closing query written where nothing can execute it.

    THE SAME DEAD END THIS FILE ALREADY REMOVED, one property over. The loop above
    re-runs checks belonging to a `partial` stage or to a debt that already carries a
    `closing_check`. Neither condition describes what a closing query IS FOR: a claim
    whose evidence has not arrived yet. A record can be `done` or `no_change_needed`
    and still rest on evidence its own author called provisional — and then no
    enumeration reaches it, so the promise is never kept.

    DEBT-105 is the archetype and it cost something measurable. Recorded
    `no_change_needed` with "Closing query: re-run the same before/after split over a
    full day of traffic on 2026-09-02", it sat four days. Run on 2026-09-06 it did not
    overturn the decision, but it corrected the number: the recorded "77% reduction"
    had compared telegram AFTER against the all-channel BEFORE. Like for like it is 39%.

    These cannot be executed — they are prose, and several wait on traffic that has not
    happened. They can be NAMED, every loop, which is the part that was missing.
    """
    promises = prose_closing_promises(data)
    if not promises:
        return
    records = sorted({record for record, _field, _text in promises})
    print(
        f"\nPROSE PROMISES: {len(promises)} closing quer(ies) in {len(records)} "
        "record(s), written where nothing can run them.\nThese are invisible to the "
        "checks above. Convert one to a `closing_check` when you touch its record:"
    )
    for record, field, text in promises:
        print(f"  {record:<46} {field.split('.')[-1][:26]:<26} {text[:60]}")


if __name__ == "__main__":
    sys.exit(main())
