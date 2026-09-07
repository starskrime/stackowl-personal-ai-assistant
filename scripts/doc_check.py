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
#: A backticked citation. `:` is IN the class so `module.py::Symbol` is seen at all;
#: `_resolve` drops the symbol half. Excluding it did not make such a citation resolve
#: to nothing — it made the citation invisible, so the document read as citing NOTHING.
_CITATION = r"`([A-Za-z0-9_./:-]+)`"

_FIELD = re.compile(r">\s*\*\*([A-Za-z ()]+):\*\*\s*(.*)")


def _header(text: str) -> dict[str, str]:
    """The blockquote header, with WRAPPED fields joined back together.

    THE THIRD TIME THIS PARSER WAS TOO NARROW, and the first time it was returning a
    wrong ANSWER rather than an honest "cannot tell". It read one line per field. The
    corpus wraps: 47 of 84 documents continue their `Source:` onto following `>` lines,
    and a continuation matched neither branch — it is not a `**Key:**` line, and it
    starts with `>` so it did not break the loop either. It was simply skipped.

    MEASURED 2026-09-06: 27 documents lost real, resolvable source paths that way, 66
    paths in total. Three of them then read FRESH while a source they DECLARE had
    changed after their verification date — D05.8 was verified 2026-08-30, the parser
    dated it by a 2026-08-29 change, and the truth was 2026-09-06. A week of drift the
    instrument reported as freshness.

    That is the distinction worth keeping: the two earlier widenings (`Source (new):`,
    src-relative paths) made documents UNMEASURABLE, which is visible and honest. This
    one made them FRESH, which is neither.
    """
    out: dict[str, str] = {}
    last: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        m = _FIELD.match(stripped)
        if m:
            last = m.group(1).strip()
            out[last] = m.group(2).strip()
        elif stripped.startswith(">") and last is not None:
            # A continuation of the field above. Blockquote marker off, text on.
            out[last] = f"{out[last]} {stripped.lstrip('>').strip()}".strip()
        elif out and not stripped.startswith(">"):
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
    # `module.py::Symbol` names a symbol INSIDE a file. The file is the thing git can
    # date, and the citation is still a citation — D09.6 named its only source that way
    # and was reported as citing nothing that exists.
    path = path.split("::", 1)[0]
    for cand in (_ROOT / path, _ROOT / "src" / "stackowl" / path):
        if cand.exists():
            return str(cand.relative_to(_ROOT))
    return None


#: A commit subject that REMOVES something. A deletion is the change most likely to
#: leave a document asserting a thing that no longer exists — D05.7 described
#: `hard_stop_enabled` for eight days after ESC-68 deleted it, and the module
#: docstring and the tests were both updated in that same commit. The document was
#: the one surface the retirement checklist did not name.
_DELETION = re.compile(r"\b(delete|retire|remove|drop)\w*\b", re.I)


def _deletions_since(paths: list[str], since: str) -> list[str]:
    """Commits since `since` that touched these paths AND removed something.

    Reported, never gated. Staleness answers WHEN a source moved; this answers
    whether it moved by SUBTRACTION, which is the case where a stale document is
    not merely behind but actively wrong.
    """
    try:
        out = subprocess.run(
            ["git", "log", f"--since={since}", "--format=%h %s", "--", *paths],
            capture_output=True, text=True, timeout=60, cwd=_ROOT,
        ).stdout.splitlines()
    except Exception as exc:  # pragma: no cover — git absent or path unreadable
        print(f"  (deletion scan unavailable: {exc})", file=sys.stderr)
        return []
    return [line for line in out if _DELETION.search(line.partition(" ")[2])]


def _sources_last_changed(paths: list[str]) -> str:
    """The commit date of the most recent change to any cited path (YYYY-MM-DD)."""
    try:
        return subprocess.run(
            ["git", "log", "-1", "--format=%cs", "--", *paths],
            capture_output=True, text=True, timeout=60, cwd=_ROOT,
        ).stdout.strip()
    except Exception:  # pragma: no cover — git absent or path unreadable
        return ""


#: A Verification COMMAND that queries the single `stackowl.jsonl` rather than the
#: `stackowl*.jsonl` glob. Always wrong, and not merely as a convenience: at UTC midnight
#: the log rotates, the gateway reopens the new file, and the CORE KEEPS AN OPEN
#: DESCRIPTOR ON THE ROTATED ONE and goes on writing there
#: (`project_gateway_core_dual_log_writer_bug`, 2026-07-18, still unfixed). A query pinned
#: to the single filename is blind to the core after any midnight — it returns 0 and looks
#: exactly like a check that ran and passed.
#:
#: MEASURED 2026-09-07: D03.2's live check read 0 on the single file and 21 on the glob,
#: and had been reading OPEN for days while the feature worked. D05.8, D08.3 and D16.1 each
#: record this lesson in PROSE, and D03.2 had it wrong anyway — which is the whole argument
#: for reporting it rather than trusting it to be remembered.
#:
#: REPORTED, NEVER GATED, for `doc_check`'s own standing reason: 29 lines across 16
#: documents carry it today, so a tripwire would fail every unrelated change until someone
#: rewrote sixteen documents. That is how a gate gets bypassed rather than satisfied. The
#: deletion-stale count went 11 -> 2 by being visible every loop; this can drain the same
#: way.
#: MATCHES EITHER ORDER, and the first version did not. It required the command word
#: BEFORE the filename and so missed `cat ~/.stackowl/logs/stackowl.jsonl | jq …`, which
#: is the commonest shape in this corpus: it reported 14 lines in 9 documents where the
#:true answer is 29 in 16. A narrowed detector under-reporting by half is the same failure
#: `doc_check` already made once, when its header parser under-reported staleness by
#: eleven.
_SINGLE_LOG = re.compile(
    r"(?:grep|jq|rg|cat|wc|log_since)\b[^\n]*stackowl\.jsonl"
    r"|stackowl\.jsonl[^\n]*\|\s*(?:grep|jq|rg|wc)\b"
)


def _single_log_queries(text: str) -> list[tuple[int, str]]:
    """(line_no, line) for every Verification command pinned to the single log file."""
    out: list[tuple[int, str]] = []
    for i, line in enumerate(text.splitlines(), 1):
        if "stackowl*.jsonl" in line:
            continue
        if _SINGLE_LOG.search(line):
            out.append((i, line.strip()))
    return out


#: An acceptance check a document itself marks OPEN. `validate_check.py` re-runs a
#: closing check only for an item whose `validate` stage is `partial`, so a document
#: that says OPEN while its item says `validate: done` holds a claim NOTHING can ever
#: re-read — the same dead end `closing_check` and `premise_check` were each built to
#: cure, one population over. MEASURED 2026-09-07: seven documents carry an OPEN
#: acceptance line and FOUR of them sit on an item marked done, so four open questions
#: were invisible to the only tool that re-asks them. D04.5 was one; it said "zero such
#: events in five days" while three had occurred, and no run could have contradicted it.
#: Anchored at line start or on a bolded `**OPEN —` so a breaker described as OPEN in
#: prose is not swept in; the raw matching lines were printed and read before this
#: number was believed.
_OPEN_CHECK = re.compile(r"^\s*\**OPEN\b|\*\*OPEN\s*[—-]", re.M)


def _open_acceptance_lines(text: str) -> list[tuple[int, str]]:
    """(line_no, line) for every acceptance check the document marks OPEN."""
    return [
        (i, line.strip())
        for i, line in enumerate(text.splitlines(), 1)
        if _OPEN_CHECK.search(line)
    ]


def _validate_stages() -> dict[str, str]:
    """item id -> its `validate` stage, or {} if progress.yml cannot be read.

    Degrades to silence rather than to a wrong report: a missing PyYAML would
    otherwise turn "four untracked open checks" into "none", which is the denominator
    error this programme pays for most.
    """
    try:
        import yaml  # noqa: PLC0415 — optional, and only this report needs it
        data = yaml.safe_load((_ROOT / "progress.yml").read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 — a report must never end a loop
        print(f"  (open-check report skipped: {exc})", file=sys.stderr)
        return {}
    return {
        str(item.get("id")): (item.get("stages") or {}).get("validate", "")
        for item in (data.get("items") or [])
    }


def main() -> int:
    docs = sorted(_DESIGNS.glob("*.md"))
    stale: list[tuple[str, str, str]] = []
    fresh = 0
    unmeasurable: list[tuple[str, str]] = []
    by_deletion: list[tuple[str, list[str]]] = []

    for doc in docs:
        head = _header(doc.read_text(encoding="utf-8"))
        verified = head.get("Last verified", "")
        source = _source_fields(head)
        date = re.search(r"(\d{4}-\d{2}-\d{2})", verified)
        # Only paths that EXIST — a document citing a moved file cannot be dated
        # by it, and guessing would be worse than saying so.
        cited = [p for p in re.findall(_CITATION, source) if "/" in p]
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
            if deleted := _deletions_since(real, date.group(1)):
                by_deletion.append((doc.name, deleted))
        else:
            fresh += 1

    print(f"design documents: {len(docs)}\n")
    if stale:
        print(f"STALE — sources changed after the document was verified ({len(stale)}):")
        for name, verified, changed in sorted(stale, key=lambda r: r[1]):
            print(f"  {name:16} verified {verified}   sources changed {changed}")
        print()
    single_log: list[tuple[str, int, str]] = []
    for doc in docs:
        for ln, line in _single_log_queries(doc.read_text(encoding="utf-8")):
            single_log.append((doc.name, ln, line))

    if by_deletion:
        print(f"STALE BY DELETION — read these first ({len(by_deletion)} of "
              f"{len(stale)}). A source these documents declare was SUBTRACTED, so "
              "each may describe something that no longer exists:")
        for name, commits in sorted(by_deletion):
            print(f"  {name}")
            for line in commits[:3]:
                print(f"        {line}")
        print()
    print(f"checked {fresh + len(stale)}, STALE {len(stale)} "
          f"({len(by_deletion)} by deletion), unmeasurable {len(unmeasurable)}")
    if single_log:
        docs_hit = len({r[0] for r in single_log})
        print(f"\nBLIND AFTER MIDNIGHT — {len(single_log)} Verification command(s) in "
              f"{docs_hit} document(s) query `stackowl.jsonl` instead of the "
              "`stackowl*.jsonl` glob. The CORE keeps writing to the ROTATED file after "
              "rotation, so these return 0 and look like a check that passed:")
        for name, ln, line in single_log[:12]:
            print(f"  {name}:{ln}  {line[:88]}")
        if len(single_log) > 12:
            print(f"  … and {len(single_log) - 12} more")

    stages = _validate_stages()
    untracked: list[tuple[str, int, str]] = []
    if stages:
        for doc in docs:
            done = stages.get(doc.stem) == "done"
            if not done:
                continue
            for ln, line in _open_acceptance_lines(doc.read_text(encoding="utf-8")):
                untracked.append((doc.name, ln, line))
    if untracked:
        docs_hit = len({r[0] for r in untracked})
        print(f"\nOPEN BUT NOT TRACKED — {len(untracked)} acceptance check(s) in "
              f"{docs_hit} document(s) say OPEN while their item says "
              "`validate: done`. `validate_check.py` only re-runs a check on a "
              "`partial` stage, so nothing will ever re-ask these:")
        for name, ln, line in untracked[:12]:
            print(f"  {name}:{ln}  {line[:88]}")
        if len(untracked) > 12:
            print(f"  … and {len(untracked) - 12} more")

    if unmeasurable:
        print("\nUNMEASURABLE — not fresh and not stale; nothing can date them:")
        for name, why in sorted(unmeasurable):
            print(f"  {name:16} {why}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
