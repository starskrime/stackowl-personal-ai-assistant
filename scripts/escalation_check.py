#!/usr/bin/env python3
"""Re-run every escalation's premise and say which ones have expired.

WHY THIS EXISTS. An item gets seven stages and a closing query. An escalation got
written once, with a measurement, and was never looked at again — so its premise
aged silently while it sat in a queue described as "the operator clears these in
one sitting".

MEASURED 2026-09-02: 31 escalations were open and SIX had already been settled.
Two were resolved by later work of mine (the channel-owned output style, the plan
that outlived its turn) and I did not close them because closing them was not
part of that item. Two expired on their own — curated decay took scout.md back
under budget, and the 92 armed rollover jobs fired and went terminal. One had
been answered and shipped. One had said RESOLVED in its own key since it was
written.

So the queue said 31 when the real number was 25, and the difference was invisible
because nothing re-checked. A question that is no longer a question still costs
the operator the time to read it and decide it is not one.

THE FIX IS THE SAME SHAPE AS `tripwires.sh`: make the rule executable. An
escalation carries a `premise_check` — a one-liner that prints HOLDS or EXPIRED —
and this runs them all. A check that cannot be written is a sign the premise is
too vague to verify, which is worth knowing on the way in.

Usage:  uv run python scripts/escalation_check.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    data = yaml.safe_load((_ROOT / "progress.yml").read_text())
    esc = (data.get("current") or {}).get("ESCALATIONS") or {}
    openq = {
        k: v for k, v in esc.items()
        if isinstance(v, dict) and not v.get("resolution")
    }
    checked = expired = 0
    unverifiable: list[str] = []

    print(f"open escalations: {len(openq)} of {len(esc)}\n")
    for key, body in sorted(openq.items()):
        check = (body.get("premise_check") or "").strip()
        if not check:
            unverifiable.append(key)
            continue
        checked += 1
        try:
            out = subprocess.run(
                check, shell=True, cwd=_ROOT, capture_output=True,
                text=True, timeout=120,
            ).stdout.strip().splitlines()
            verdict = out[-1] if out else "(no output)"
        except Exception as exc:  # noqa: BLE001 — a check may not break the sweep
            verdict = f"(check failed: {exc})"
        if verdict.startswith("EXPIRED"):
            expired += 1
            print(f"  EXPIRED  {key}\n           {verdict}")
        else:
            print(f"  holds    {key}  [{verdict[:60]}]")

    print(f"\nchecked {checked}, EXPIRED {expired}, no premise_check {len(unverifiable)}")
    if unverifiable:
        print("\nThese cannot be re-verified — a premise with no check ages invisibly,")
        print("which is exactly how six of them went stale:")
        for k in unverifiable:
            print(f"   {k}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
