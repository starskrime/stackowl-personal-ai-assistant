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

import os
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
#: `/tests/audit/` and this file itself are excluded for the same reason: both hold
#: EXPLANATORY text about the finding rather than the finding. The audit tests quote the
#: corpus verbatim as fixtures; this script's own docstring has to say "ESC-20 is
#: genuinely open" in order to tell a reader what the report means. That was the FOURTH
#: false-positive shape, and it appeared the moment the report was rewritten to explain
#: itself — a detector that reads prose will always eventually read its own.
_SKIP = (
    "do_not_push_to_git_research_only", "/.git/", "docs_archive", "_bmad-output",
    "graphify-out", "/tests/audit/", "/scripts/escalation_check.py",
)

#: Directory names pruned during the walk, never entered. MEASURED 2026-09-07: the first
#: version filtered paths AFTER `rglob("*")` had already traversed them — 122,273 paths
#: and 48,849 candidate files, taking **48.3 seconds on every run of this script**,
#: almost all of it reading `.venv` (5.7 GB). Pruning is not only speed: a vendored
#: library docstring that happens to say "ESC-12 is open" would have been REPORTED as a
#: finding in this repo's own record.
_PRUNE = {
    ".venv", "venv", ".git", ".uv-cache", "node_modules", "__pycache__", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", "graphify-out", "do_not_push_to_git_research_only",
    "_bmad-output", "htmlcov", ".tox", "dist", "build", ".idea", ".vscode",
}

#: Every one of these was MEASURED, not assumed. `.venv` (5.7 GB, 18,988 candidate
#: files) and `.uv-cache` (5.8 GB, 21,960) are the whole cost: 48.3s -> 2.3s. The
#: dot-directories that REMAIN are the point — `.claude/skills/` is a live instruction
#: surface the loop reads on every invocation, `.agents` and `.github` likewise, and
#: pruning them by prefix would have taken all three dark to save nothing.


def _still_called_open(live: set[int]) -> list[tuple[str, int, list[int], str]]:
    """Sites calling an escalation open whose id is no longer in the queue.

    IT REPORTS A FACT, NOT A VERDICT, and the first version of this got that wrong. It
    was called ANSWERED BUT STILL CALLED OPEN, which asserts the citation is the stale
    half. **Measured 2026-09-07, one loop later, that was true of only three of five
    sites.** Two describe ESC-20's terse-compression half, which is GENUINELY OPEN —
    "STILL OPEN: whether a scheduled briefing should be compressed at all", pinned by
    `test_terse_does_NOT_compress_a_scheduled_briefing` — and had simply FALLEN OUT of
    the queue. A reader trusting the old name would have deleted the last live record of
    an unanswered product question and called it tidying. Same error one level up as the
    empty table this codebase already names: absence is a QUESTION, not an answer.

    So the two things it can mean are named, and neither is assumed:
      * the escalation was ANSWERED and the citation went stale — fix the citation;
      * the citation is RIGHT and the entry was LOST — restore the entry.

    WHY ENTRIES GO MISSING AT ALL, which is the root cause and not a slip. The queue
    changed convention: it used to be a LIST that was PRUNED on answer, and is now a DICT
    that RETAINS the entry with a `resolution` (which is how `openq` above filters). The
    restructure in `480571bc` (2026-08-30) dropped every pruned entry — and ESC-20, only
    HALF answered, went with them. A half-answered escalation had no home under the old
    convention: the moment any part resolved, the whole entry looked resolved.

    WHAT IT COST, measured: ESC-25 was answered by Bakir on 2026-08-21 and D04.1 called
    it open for seventeen days. ESC-22 was answered the same day — "deliver the floor
    already earned" — and `test_escalation_that_changes_nothing_is_visible.py` still
    opens by saying it is "open with Bakir" while its own
    `test_the_identical_re_run_IS_SKIPPED` records the answer 85 lines later. That is the
    SECOND file found contradicting itself this way.

    63 dangling ids are cited across the tree and MOST are correct: an answer recorded
    at the site it changed is exactly right. Only a PRESENT-TENSE open claim is a
    finding, which is why this reads the sentence and not just the id.
    """
    out: list[tuple[str, int, list[int], str]] = []
    for dirpath, dirnames, filenames in os.walk(_ROOT):
        # Prune by NAME only. An earlier version also dropped every dot-directory,
        # which would have made `.claude/skills/` invisible — a LIVE INSTRUCTION surface
        # the loop reads on every invocation. The caches that matter are named below;
        # blanket-excluding a prefix is how a surface goes dark without anyone choosing it.
        dirnames[:] = sorted(d for d in dirnames if d not in _PRUNE)
        for name in sorted(filenames):
            path = Path(dirpath) / name
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


#: The key shapes this corpus uses to say "recorded, not verified".
_UNVERIFIED_CLAIM = re.compile(r"NOT_VERIFIED|UNVERIFIED|RECORDED_NOT", re.I)


def _sweep_recorded_claims(current: dict) -> None:
    """The FOURTH population of aging claims, and the first three name the cure.

    An escalation's premise aged silently until `premise_check`. A `partial` stage's
    evidence aged until `closing_check`. A design document's claim aged until
    `doc_check` made `Last verified` checkable. A RECORDED PRODUCT CLAIM got nothing:
    it is prose in `current`, re-read every loop, and never marked settled.

    MEASURED 2026-09-09 on three of my own loops. One record's
    `THE_REST_RECORDED_NOT_VERIFIED` lists four defects a lens found but did not
    verify. I picked one each loop and spent real measurement finding that two are
    FIXED (one live-evidenced by three `loses_an_occurrence` replays, the last being
    `morning_brief`) and two are the OPERATOR'S open questions (ESC-127, ESC-160). Zero
    were live unverified defects, and the record still read as four open items, because
    nothing could mark a claim settled.

    SEVEN records carry such a list, so this is a population rather than one entry —
    measured before writing a line of it, because a cure for a population of one is
    over-building.

    REPORTED, NOT GATED, and the count is printed so it can only fall: failing every
    unrelated change until seven historical records are retrofitted is how a gate gets
    bypassed rather than satisfied. Same reasoning `doc_check` already applies to
    staleness and `progress_lint` to the 43 legacy validates.
    """
    records = {
        key: body
        for key, body in current.items()
        if isinstance(body, dict) and any(_UNVERIFIED_CLAIM.search(k) for k in body)
    }
    if not records:
        return
    with_check = {k: v for k, v in records.items() if (v.get("claim_check") or "").strip()}
    print(
        f"\nRECORDED BUT NEVER SETTLED — {len(records)} record(s) in `current` carry a "
        f"claim list marked unverified; {len(with_check)} carry a runnable "
        "`claim_check`. A claim nothing can settle is re-measured by the next loop that "
        "reads it, which has already cost three."
    )
    for key, body in sorted(with_check.items()):
        check = body["claim_check"].strip()
        try:
            out = subprocess.run(
                check, shell=True, cwd=_ROOT, capture_output=True, text=True, timeout=180,
            ).stdout.strip().splitlines()
            verdict = out[-1] if out else "(no output)"
        except Exception as exc:  # noqa: BLE001 — a check may not break the sweep
            verdict = f"(check failed: {exc})"
        mark = "SETTLED " if verdict.startswith("SETTLED") else "open    "
        print(f"  {mark} {key[:64]}\n           {verdict[:150]}")
    for key in sorted(set(records) - set(with_check)):
        print(f"  no check {key[:64]}")


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

    _sweep_recorded_claims((data.get("current") or {}))

    live = {int(m.group(1)) for k in esc if (m := _ESC_REF.search(str(k)))}
    if stale := _still_called_open(live):
        ids = sorted({n for _f, _l, dang, _t in stale for n in dang})
        print(f"\nCITED AS OPEN, ABSENT FROM THE QUEUE — {len(stale)} site(s) describe "
              f"{len(ids)} escalation(s) as open whose id is not in `ESCALATIONS` "
              f"{['ESC-%d' % n for n in ids]}. Read each before acting: EITHER it was "
              "answered and the citation is stale (fix the citation), OR the citation "
              "is right and the entry was LOST (restore the entry, with a "
              "premise_check). Do not assume the first — ESC-20 was the second:")
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
