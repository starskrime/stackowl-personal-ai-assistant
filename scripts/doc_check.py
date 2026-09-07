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
_LOGS = Path.home() / ".stackowl" / "logs"

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
#: (`project_gateway_core_dual_log_writer_bug`, 2026-07-18 — FIXED IN CODE by
#: DEBT-196 (`25568c8a`) and NOT YET PROVEN — no UTC midnight has passed since it
#: shipped, so the follow branch has run zero times. Read the glob regardless: the
#: retained logs still hold the misplaced records, and one file is blind to every
#: previous day anyway). A query pinned
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


#: A statement that some check in this document was CLOSED or RESOLVED. Its presence
#: does not prove THIS check closed — it says which of two cases the reader is in, and
#: that distinction is the whole value. MEASURED 2026-09-07 over the three live sites:
#: D13.1 marks a check **OPEN** and says **CLOSED 2026-09-06** TWELVE LINES BELOW it —
#: a status marker written before the outcome, with the outcome appended underneath and
#: the marker never updated. D05.8's two rows carry no close statement anywhere in the
#: document and are genuinely waiting ("0 planned envelopes ran"). Same file, same
#: report, opposite actions. Reporting the FACT and not the verdict is the lesson the
#: escalation report had to learn the hard way one loop earlier.
_CLOSE_STATEMENT = re.compile(r"\*\*(CLOSED|RESOLVED)\b", re.I)


#: A log-reading Verification command, and a date recorded beside it.
_LOG_QUERY = re.compile(r"(?:grep|jq|cat|wc|log_since)[^\n]*stackowl[^\n]*\.jsonl")
_DATED_RUN = re.compile(
    r"(?:PASS|MEASURED|RAN|RE-RAN|Measured|Re-ran)\s+(\d{4}-\d{2}-\d{2})"
)


#: A test path as a document writes it.
_TEST_PATH = re.compile(r"(tests/[A-Za-z0-9_./-]+\.py)")


def _test_paths_on_command_lines(text: str) -> list[tuple[int, str, str]]:
    """(line_no, path, line) for every test path a reader would actually RUN.

    THE SCOPING IS THE WHOLE DESIGN, and it replaces a negation regex that could not
    have worked. A document naming a deleted test is a real defect — D01.7 advertised
    `tests/memory/test_authored_once_promotion.py` for 24 days after `f3d0d85a` removed
    it, through a stamp claiming the section had been RUN — but MEASURED 2026-09-07,
    every missing path in this corpus sits in PROSE THAT DENIES IT: five sites, five
    different phrasings ("DOES NOT EXIST", "does not exist either", "There is also no
    X", "HAS NOT EXISTED SINCE"). `map_freshness._DENIAL` is the careful version of that
    regex and it matches only THREE of the five, so the best available negation rule
    would have shipped a report that is 40% wrong on a corpus with ZERO real defects.

    So this does not read negation at all. A path counts only on a RUNNABLE COMMAND LINE
    — inside a fence, not a `#` comment — because that is where a path a reader will
    execute lives, and every denial is prose or a comment BY CONSTRUCTION. Verified both
    ways: the rule sees 133 paths across the corpus so it is not blind, reports 0 today,
    and against D01.7 at `2e5736b9^` it names line 387 — the real defect, caught in one
    second where the live one took 24 days and a chance `--collect-only`.
    """
    out: list[tuple[int, str, str]] = []
    fenced = False
    for i, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if not fenced or line.lstrip().startswith("#"):
            continue
        out.extend((i, m.group(1), line.strip()) for m in _TEST_PATH.finditer(line))
    return out


#: A pipe that takes one record by POSITION rather than by time.
_BY_POSITION = re.compile(r"\|\s*(?:tail|head)\b")
#: The readers a log query can be built on. Only `grep` is unordered (see below).
_READER = re.compile(r"\b(grep|ugrep|cat|jq|wc|log_since\.sh)\b")


def _shell_skeleton(cmd: str) -> str:
    """`cmd` with the CONTENTS of quoted spans blanked out, lengths preserved.

    Needed because the thing being matched is shell STRUCTURE — which reader owns the
    glob, and whether a pipe follows it — and a grep PATTERN is free to contain both.
    The first version of this detector used `[^|]*` to keep the reader and its glob in
    one pipeline segment, and it silently missed D16.5, whose pattern is
    `'(discover_tools|register_server_tools)'`: a regex alternation inside quotes read
    as a shell pipe. Blanking the quotes first makes the two unconfusable instead of
    making the regex cleverer.
    """
    out = list(cmd)
    quote = ""
    for k, ch in enumerate(cmd):
        if quote:
            if ch == quote:
                quote = ""
            else:
                out[k] = " "
        elif ch in "'\"":
            quote = ch
    return "".join(out)


def _newest_by_pipe_position(text: str) -> list[tuple[int, str, bool]]:
    """(line_no, command, is_sorted) for every `grep <glob> | tail`-shaped command.

    `is_sorted` is returned rather than filtered so the caller can print a DENOMINATOR.
    A detector that reports nothing and a detector that is blind look identical from the
    outside, and this corpus has already paid for that once.

    MEASURED 2026-09-07, on a measurement this loop had just made and was about to
    write into a document. `grep -h <pattern> ~/.stackowl/logs/stackowl*.jsonl | tail -1`
    is the idiom for "the newest record", and it is only correct if grep emits files in
    ARGUMENT order. Under the harness this programme actually runs in, `grep` is a shell
    function wrapping a MULTI-THREADED grep, which emits results as workers finish:
    three identical invocations of that exact command returned last-line timestamps
    2026-09-03, 2026-09-04 and 2026-09-03, while the true newest record was 2026-09-07.
    The current log file landed SEVENTH of eleven in the output stream.

    So this is not a stylistic note. The single-file form the `BLIND AFTER MIDNIGHT`
    report exists to eliminate is at least ORDERED; the glob that replaces it is not, and
    the cure for one report created the exposure for this one. A COUNT is unaffected —
    which is exactly why it hid, because every count in this corpus is right.

    THE CURE IS ONE WORD: `| sort |` before the `tail`. Every line these logs contain
    begins `{"ts": "` (verified over the two largest retained files: 0 non-conforming
    lines), so a lexical sort IS a chronological sort, and it is correct under an ordered
    grep too. That invariant is asserted in the tripwire beside this, because the day a
    writer moves `ts` off the front the cure stops working silently.

    SCOPED TO `grep`, and the scope is a measurement, not caution. `cat f1 f2 | jq` and
    `jq -c ... f1 f2` both consume their arguments in order, so the four `cat`/`jq` sites
    in this corpus are correct as written and are NOT reported. Reporting them would be
    the cry-wolf failure that gets a detector bypassed.
    """
    out: list[tuple[int, str, bool]] = []
    fenced = False
    pending: list[str] = []
    start = 0
    for i, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            pending = []
            continue
        if not fenced or line.lstrip().startswith("#"):
            pending = []
            continue
        # A command may be split across lines with a trailing backslash, and both halves
        # matter: D16.5 carries the `grep` on one line and the glob AND the `| tail` on
        # the next, so a per-line reader would miss it entirely.
        if not pending:
            start = i
        pending.append(line.rstrip())
        if line.rstrip().endswith("\\"):
            continue
        cmd = " ".join(x.rstrip("\\").strip() for x in pending)
        pending = []
        skeleton = _shell_skeleton(cmd)
        m = re.search(r"stackowl\*\.jsonl", skeleton)
        if m is None:
            continue
        readers = _READER.findall(skeleton[: m.start()])
        if not readers or readers[-1] not in ("grep", "ugrep"):
            continue
        after = skeleton[m.end():]
        if _BY_POSITION.search(after):
            out.append((start, cmd, "sort" in after))
    return out


def _log_horizon() -> str:
    """The oldest date the retained logs can still answer for, or "" if unknowable.

    Read from the FILES rather than from `STACKOWL_LOG_RETAIN_DAYS`, because the
    configured number is what retention INTENDS and the files are what it achieved.
    MEASURED 2026-09-07: backupCount is 30 and `getFilesToDelete()` returns nothing, yet
    only ten dated files exist — the horizon is young, not over-pruned, because a
    deletion incident on 2026-08-30 left two and daily rotation has added one since. A
    detector keyed on the intended 30 would have reported nothing while a third of these
    documents cited evidence that is already gone.
    """
    names = sorted(p.name for p in _LOGS.glob("stackowl-*.jsonl")) if _LOGS.exists() else []
    return names[0][len("stackowl-"):-len(".jsonl")] if names else ""


def _evidence_older_than_the_logs(text: str, horizon: str) -> list[tuple[int, str, str]]:
    """(line_no, newest recorded date, command) for checks nothing can reproduce.

    A Verification command that reads the logs and records "PASS 2026-08-21: 8
    occurrences" is a CHECK for as long as the logs reach back that far, and a RECORD
    afterwards. Once the evidence rotates away the command returns 0 forever, and 0 reads
    as failure to anyone who runs it — the ambiguous-zero trap with a cause of its own:
    THE EVIDENCE HAD A SHORTER LIFETIME THAN THE DOCUMENT.

    MEASURED 2026-09-07: four such commands across three documents, the oldest citing
    2026-07-27 against logs that begin 2026-08-28. D16.3's is the sharpest — its evidence
    was a throwaway plugin installed to prove the path and then REMOVED, so the line it
    greps cannot fire again even in principle.

    A fresher date anywhere in the same block clears it, which is not a nicety: D08.1
    carries "RAN 2026-08-17 -> 4 firings" with "RE-RAN 2026-09-07 -> 99" on the next
    line, and flagging that would be crying wolf on a document that had already done the
    work.
    """
    if not horizon:
        return []
    lines = text.splitlines()
    out: list[tuple[int, str, str]] = []
    fenced = False
    for i, line in enumerate(lines):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        # ONLY INSIDE A FENCE. A Verification command is one a reader would RUN; a
        # command quoted in prose is a description of one. The distinction is not
        # pedantic — the document that FIXES this defect has to quote the retired
        # command to explain it, and a line-scoped detector flagged that correction as
        # the defect. Same shape as the escalation report reading its own explanation.
        if not fenced or not _LOG_QUERY.search(line):
            continue
        dates = _DATED_RUN.findall("\n".join(lines[i:i + 10]))
        if dates and max(dates) < horizon:
            out.append((i + 1, max(dates), line.strip()))
    return out


def _close_note(body: str, open_line: int) -> str:
    """Does this document record a close BELOW the open marker, and where?

    A separate function because the live population cannot exercise both branches: after
    D13.1 was fixed only D05.8 remains, and both of its rows are the "no close" case. A
    mutation that hardcoded the answer changed NOTHING in the report — measured, on the
    first attempt to prove it. A branch the corpus cannot reach is a branch only a
    synthetic test can guard.
    """
    below = [
        i for i, line in enumerate(body.splitlines(), 1)
        if i > open_line and _CLOSE_STATEMENT.search(line)
    ]
    if below:
        return f"document also says CLOSED/RESOLVED at line {below[0]}"
    return "no close statement anywhere in this document"


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
              "`stackowl*.jsonl` glob, so they see only TODAY and return 0 for anything "
              "older — which reads as *not yet*, never as *wrong instrument*. The cause "
              "that made this worse is FIXED (DEBT-196: a second process now follows a "
              "rotation instead of writing on into the renamed file), but the retained "
              "logs still hold the misplaced records, and a single-file query is blind "
              "to every previous day regardless:")
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
            body = doc.read_text(encoding="utf-8")
            for ln, line in _open_acceptance_lines(body):
                untracked.append((doc.name, ln, f"{line}\n        -> {_close_note(body, ln)}"))
    if untracked:
        docs_hit = len({r[0] for r in untracked})
        print(f"\nOPEN BUT NOT TRACKED — {len(untracked)} acceptance check(s) in "
              f"{docs_hit} document(s) say OPEN while their item says "
              "`validate: done`. `validate_check.py` only re-runs a check on a "
              "`partial` stage, so nothing will ever re-ask these:")
        print("     Each line says whether the SAME document also records a close — a "
              "fact, not a verdict: a marker superseded by a CLOSED line below it is "
              "stale prose, while one with no close anywhere is a real open question.")
        for name, ln, line in untracked[:12]:
            head, _, note = line.partition("\n")
            print(f"  {name}:{ln}  {head[:88]}")
            print(f"      {note.strip()}")
        if len(untracked) > 12:
            print(f"  … and {len(untracked) - 12} more")

    horizon = _log_horizon()
    rotted: list[tuple[str, int, str, str]] = []
    for doc in docs:
        for ln, when, cmd in _evidence_older_than_the_logs(
            doc.read_text(encoding="utf-8"), horizon
        ):
            rotted.append((doc.name, ln, when, cmd))
    if rotted:
        print(f"\nEVIDENCE OLDER THAN THE LOGS — {len(rotted)} Verification command(s) "
              f"in {len({r[0] for r in rotted})} document(s) record a result from BEFORE "
              f"the oldest retained log ({horizon}). Re-running them cannot reproduce "
              "what they claim: they return 0, and 0 reads as failure. These are RECORDS "
              "now, not checks — date them as such, and give the reader something "
              "runnable beside them:")
        for name, ln, when, cmd in rotted[:12]:
            print(f"  {name}:{ln}  recorded {when}")
            print(f"      {cmd[:88]}")

    unordered: list[tuple[str, int, str]] = []
    by_position = 0
    for doc in docs:
        for ln, cmd, is_sorted in _newest_by_pipe_position(doc.read_text(encoding="utf-8")):
            by_position += 1
            if not is_sorted:
                unordered.append((doc.name, ln, cmd))
    if unordered:
        print(f"\nNEWEST RECORD TAKEN BY PIPE POSITION — {len(unordered)} Verification "
              f"command(s) in {len({u[0] for u in unordered})} document(s) pipe a "
              "MULTI-FILE `grep` straight into `tail`/`head`, which returns the newest "
              "record only if grep emits files in argument order. The grep this "
              "programme actually runs under is multi-threaded and does not: three "
              "identical runs of one such command returned 2026-09-03, 2026-09-04 and "
              "2026-09-03 while the true newest record was 2026-09-07. Counts are "
              "unaffected, which is why this hid. Add `| sort |` before the `tail` — "
              "every log line begins `{\"ts\": \"`, so a lexical sort is a "
              "chronological one, and it is correct under an ordered grep too:")
        for name, ln, cmd in unordered[:12]:
            print(f"  {name}:{ln}  {cmd[:96]}")
        if len(unordered) > 12:
            print(f"  … and {len(unordered) - 12} more")
    elif by_position:
        print(f"\nNEWEST RECORD TAKEN BY PIPE POSITION — none, across {by_position} "
              "command(s) that take a record by position from the multi-file glob; every "
              "one of them sorts first. (The denominator is printed because a silent "
              "detector and a clean corpus look identical.)")

    watched = 0
    gone: list[tuple[str, int, str, str]] = []
    for doc in docs:
        for ln, rel, line in _test_paths_on_command_lines(
            doc.read_text(encoding="utf-8")
        ):
            watched += 1
            if not (_ROOT / rel).exists():
                gone.append((doc.name, ln, rel, line))
    if gone:
        print(f"\nNAMES A TEST THAT IS GONE — {len(gone)} of {watched} test path(s) on a "
              f"runnable Verification command do not exist. A step naming a file this "
              "tree does not have CANNOT have been run, so any 'Verification RUN' stamp "
              "above it claims more than was done:")
        for name, ln, rel, line in gone[:12]:
            print(f"  {name}:{ln}  {rel}")
            print(f"      {line[:88]}")
    elif watched:
        print(f"\nNAMES A TEST THAT IS GONE — none, across {watched} test path(s) on "
              "runnable Verification commands. (The denominator is printed because a "
              "silent detector and a clean corpus look identical.)")

    if unmeasurable:
        print("\nUNMEASURABLE — not fresh and not stale; nothing can date them:")
        for name, why in sorted(unmeasurable):
            print(f"  {name:16} {why}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
