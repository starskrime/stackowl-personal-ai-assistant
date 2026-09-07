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

import re
import subprocess
import sys
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parent.parent

#: An escalation id cited anywhere in the tree.
_ESC_REF = re.compile(r"ESC[-_](\d+)")

#: The id described as open RIGHT NOW. `(?<![-_\w])` keeps "fail-open" out — it was
#: the first false positive this detector produced, on `durable/loop.py`. The
#: negative lookahead keeps "open circuit" out, which is a breaker, not a question.
_OPEN_NOW = re.compile(
    r"(?<![-_\w])open\b(?!\s*(?:circuit|-))|(?<![-_\w])unanswered\b", re.I
)

#: "…asserted while ESC-23 was open" is a correct HISTORICAL note and must not flag.
#: This was the second false positive, and the two together are why the detector is a
#: report rather than a gate: past and present tense are one auxiliary verb apart.
_PAST_TENSE = re.compile(r"\b(?:was|were|while|when|until|had been)\b[^.]{0,40}\bopen\b", re.I)

#: A paired double-quoted span. THE THIRD FALSE POSITIVE, and this one the detector
#: produced against the very document that fixes the first case it found: a correction
#: QUOTES the stale claim it is retiring — *"the order question is still open"* — and a
#: reader of words cannot tell a quotation from an assertion. Quoted spans are stripped
#: before the open-test, never before the id-scan, so a claim genuinely made inside
#: quotes still carries its id. `'''`/`"""` docstring fences do not pair and are left
#: alone. This is the third shape in a row that separates a finding from a correct
#: sentence by one token, and the third reason this is a report and not a gate.
#:
#: IT MUST SPAN NEWLINES. Prose wraps, so a quotation opens on one line and closes on
#: the next — measured on the very first run of the line-local version, which still
#: flagged the corrected D04.1 because only the closing half of *"…still open"* sat on
#: the flagged line. `_blank_quotes` blanks the span but KEEPS the newlines, so line
#: numbers survive and a line-by-line walk still reports the right place.
_QUOTED = re.compile(r'(?<!")"(?!")[^"]{1,300}"(?!")|\*"[^"]{1,300}"\*')


def _blank_quotes(text: str) -> str:
    """Replace quoted spans with spaces, preserving every newline and offset."""
    return _QUOTED.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)

#: Records, not live instruction. Rewriting them would falsify history — the boundary
#: `CLAUDE.md` draws for the "it hangs" sweep. `tests/audit/` is excluded for a
#: different reason: those files quote the corpus verbatim as FIXTURES, so a detector
#: scanning them reports its own test data back to itself.
_SKIP = (
    "do_not_push_to_git_research_only", "/.git/", "docs_archive", "_bmad-output",
    "graphify-out", "/tests/audit/",
)


def _still_called_open(live: set[int]) -> list[tuple[str, int, list[int], str]]:
    """Sites calling an escalation open that is no longer in the queue.

    WHY THIS IS THE SAME CURE AGAIN. An OPEN escalation has a `premise_check` and this
    script re-runs it. An ANSWERED one is DELETED from the queue — so nothing re-reads
    the sites that cited it, and every one of them keeps asking a question the operator
    already settled. `tests/audit/test_a_settled_decision_is_not_contradicted_by_the_record`
    named this exact gap in its own docstring and deferred building it.

    MEASURED 2026-09-07, and the cost was 17 days: ESC-25 was answered by Bakir on
    2026-08-21 — "dispatch first, on both loops, deliberately", his call against my
    recommendation, removed from the queue in `3306a340` and pinned by
    `test_dispatch_precedes_the_callback_on_both_BY_DECISION`. D04.1 went on calling it
    OPEN in three places, and `test_both_tool_loops_conform.py` contradicted ITSELF —
    line 180 "the order question is still open", line 212 "ANSWERED 2026-08-21".

    63 dangling ids are cited across the tree and MOST are correct: an answer recorded
    at the site it changed is exactly right. Only a PRESENT-TENSE open claim is a
    finding, which is why this reads the sentence and not just the id.
    """
    out: list[tuple[str, int, list[int], str]] = []
    for path in sorted(_ROOT.rglob("*")):
        rel = str(path.relative_to(_ROOT))
        if path.suffix not in (".md", ".py", ".yml"):
            continue
        if rel == "progress.yml" or any(s in f"/{rel}" for s in _SKIP):
            continue
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        lines = raw.splitlines()
        unquoted_lines = _blank_quotes(raw).splitlines()
        for i, line in enumerate(lines, 1):
            ids = [int(n) for n in _ESC_REF.findall(line)]
            unquoted = unquoted_lines[i - 1] if i <= len(unquoted_lines) else line
            if not ids or not _OPEN_NOW.search(unquoted) or _PAST_TENSE.search(unquoted):
                continue
            if dangling := [n for n in ids if n not in live]:
                out.append((rel, i, dangling, line.strip()))
    return out


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

    live = {int(m.group(1)) for k in esc if (m := _ESC_REF.search(str(k)))}
    if stale := _still_called_open(live):
        ids = sorted({n for _f, _l, dang, _t in stale for n in dang})
        print(f"\nANSWERED BUT STILL CALLED OPEN — {len(stale)} site(s) describe "
              f"{len(ids)} escalation(s) as open that are no longer in the queue "
              f"{['ESC-%d' % n for n in ids]}. An answered escalation is DELETED from "
              "the queue, so nothing re-reads what cited it — the operator is asked "
              "again for a decision he already made:")
        for rel, ln, dang, text in stale:
            print(f"  {rel}:{ln}  {['ESC-%d' % n for n in dang]}")
            print(f"        {text[:96]}")
    if unverifiable:
        print("\nThese cannot be re-verified — a premise with no check ages invisibly,")
        print("which is exactly how six of them went stale:")
        for k in unverifiable:
            print(f"   {k}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
