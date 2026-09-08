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


#: A commit examined by a human and found not to touch a document's claims.
#: The header spells it ``> **Reviewed:** <sha> — <why it does not apply>``.
#:
#: BACKTICKS ARE REQUIRED, not decoration. A bare `\b[0-9a-f]{7,40}\b` also matches
#: ordinary English written in the letters a-f — "defaced", "efface", "acceded" — so a
#: sentence explaining a dismissal could silently become a second sha, and the guard
#: below would then reject the document for a word.
_REVIEWED_SHA = re.compile(r"`([0-9a-f]{7,40})`")


def _reviewed_shas(head: dict[str, str]) -> set[str]:
    """Short shas this document records as READ AND DISMISSED.

    WHY AN OPT-OUT EXISTS AT ALL, and why it is this shape. `Last verified: <date>,
    against commit <sha>` conflates two different facts — WHEN the document's claims
    were checked, and WHICH TREE they were checked against. An unrelated commit to a
    cited file moves the second and leaves the first untouched, so the honest answer
    is neither "re-run the whole Verification section" nor "bump the date".

    MEASURED 2026-09-08, which is what forced the distinction: FIVE documents went
    stale on ONE commit, `1f48d999`, a scheduler-drain fix. All five cite
    `startup/orchestrator.py` — 4,839 lines that wire the entire platform — and four
    of the five cite it only as the place their subject is WIRED. Re-running five
    Verification sections to discover that a drain fix does not affect prompt-cache
    breakpoints is a cost high enough that the dishonest response (bump the date)
    becomes the likely one. That is how documents rot: not because anyone chose to
    lie, but because the truthful act was made expensive.

    THE CEILING IS BUILT IN, and deliberately so — this repo has already learned that
    an opt-out with no ceiling eats the rule it exempts. A `Reviewed:` entry names ONE
    COMMIT that already exists. It cannot pre-authorise a future change, it expires by
    construction the moment anything else lands, and
    `tests/audit/test_a_reviewed_commit_is_a_real_commit.py` refuses a sha that is not
    a real commit touching a cited source. A prose whitelist could do none of that.
    """
    # ONLY THE DISMISSAL POSITION COUNTS — the text before the first em dash, which
    # is exactly the documented shape `Reviewed: <sha> — <why it does not apply>`.
    #
    # MEASURED 2026-09-09, when the gate refused a commit of mine and was RIGHT to.
    # D13.2's reason names an EARLIER commit as history ("the second time a
    # `PROCESS.md` edit has marked it stale without touching it — the header already
    # records `2f899198` doing the same"), and reading the whole field turned that
    # citation into a second dismissal, of a commit that predates the verification.
    # The guard then correctly reported a stale entry that did not exist.
    #
    # A reason has to be able to CITE a commit without DISMISSING it, or the useful
    # half of the explanation gets written out to keep the guard quiet — which is how
    # a mechanism starts degrading the record it exists to protect.
    field = head.get("Reviewed", "")
    dismissal = field.split("—", 1)[0] if "—" in field else field
    return {m.group(1)[:8] for m in _REVIEWED_SHA.finditer(dismissal)}


def _changes_since(
    paths: list[str], verified: str
) -> list[tuple[str, str, str, list[str]]]:
    """(date, sha, subject, which cited paths) for every change after `verified`.

    ONE QUERY IS THE POINT, not an optimisation. The report previously took the
    staleness DATE from one git call and the EXPLANATION from another; a report that
    names a cause its detector did not use is worse than one that names none, and two
    date comparisons of different shapes will eventually disagree. (`--since` reads
    LOCAL time and has already hidden this loop's own commits once, so the comparison
    here is lexical on `%cs`, the same field the verdict is stated in.)

    THE CULPRIT PATH IS ONLY HALF AN ANSWER, and the subject is the other half.
    Knowing `orchestrator.py` changed says nothing about whether the change concerned
    you; "fix(restart): the drain asked whether anything was running" is instantly
    irrelevant to a document about prompt caching and instantly relevant to one about
    restarts. That judgement is the entire cost of draining this report.
    """
    if not paths or not verified:
        return []
    try:
        out = subprocess.run(
            ["git", "log", "--format=%x1e%cs\x1f%h\x1f%s", "--name-only", "--", *paths],
            capture_output=True, text=True, timeout=180, cwd=_ROOT,
        ).stdout
    except Exception:  # pragma: no cover — git absent
        return []
    wanted = set(paths)
    changes: list[tuple[str, str, str, list[str]]] = []
    for record in out.split("\x1e"):
        if not record.strip():
            continue
        head, _, rest = record.partition("\n")
        parts = head.split("\x1f", 2)
        if len(parts) != 3 or parts[0] <= verified:
            continue
        touched = sorted(f for f in rest.split("\n") if f in wanted)
        changes.append((parts[0], parts[1], parts[2], touched))
    return changes


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

#: The same shape with the filename left open, so it matches the glob spelling too.
#: This is the DENOMINATOR — see `_log_query_lines`.
_ANY_LOG = re.compile(
    r"(?:grep|jq|rg|cat|wc|log_since)\b[^\n]*stackowl[^\s]*\.jsonl"
    r"|stackowl[^\s]*\.jsonl[^\n]*\|\s*(?:grep|jq|rg|wc)\b"
)


def _log_query_lines(text: str) -> list[tuple[int, str]]:
    """(line_no, line) for EVERY Verification command that reads a log file at all.

    The denominator for BLIND AFTER MIDNIGHT. Without it a corpus where every command
    globs correctly and a corpus this finder cannot parse print the same empty report —
    the ambiguity that let EVIDENCE OLDER THAN THE LOGS read zero for a whole loop while
    it was blind. `_ANY_LOG` is `_SINGLE_LOG` with the filename left open, so it sees both spellings.
    """
    return [
        (i, line.strip())
        for i, line in enumerate(text.splitlines(), 1)
        if _ANY_LOG.search(line)
    ]


#: An explicit opt-out for a query that reads the single log file ON PURPOSE.
#:
#: NOT EVERY SINGLE-FILE QUERY IS BLIND, and treating them alike made this report's
#: own number wrong. MEASURED 2026-09-08 by reading all 31 flagged commands in
#: context: SEVEN are deliberately scoped to the current boot and are CORRECT as
#: they stand — three sit directly under `./start.sh && sleep 90` asking "did this
#: boot log any ERROR", one says "ZERO turns have run since the restart", and two
#: ask "does the arithmetic reconcile on the current boot?", where widening to the
#: glob would sum eleven days of registrations against a one-boot denominator and
#: silently break a correct check. So the honest population was 24, not 31, and a
#: future loop reading "31" would have "fixed" seven working queries.
#:
#: A STRUCTURED TOKEN, NOT A PROSE WHITELIST. The tempting detector is "does a
#: nearby comment say 'current boot'", which is a guess at future phrasing — the
#: failure this programme has paid for repeatedly. An explicit marker is declared
#: by the author, cannot drift with wording, and shows up in a grep.
_ON_PURPOSE = "doc_check: current-boot"


def _fenced_log_commands(text: str) -> list[tuple[int, str]]:
    """(line_no, joined command) for every log-reading command INSIDE a fence.

    WALKS FENCES AND JOINS CONTINUATIONS, and both halves were missing here.

    The first version scanned raw lines, which got this wrong in BOTH directions
    and the file already knew it. `_fence_commands` exists precisely because
    "FIFTEEN fenced blocks in this corpus name a `.jsonl` log and are invisible to
    a per-line matcher, because the reader sits on one line and the glob on the
    next", and `_rotted_evidence`'s fence walk exists because "a command quoted in
    prose is a description of one... a line-scoped detector flagged that
    correction as the defect". Two sibling detectors had both cures; this one had
    neither, so it was wired on only some paths — this repo's failure mode #1.

    MEASURED 2026-09-08, which is how it was found: 23 lines name the single log
    and go unflagged. NINE are real commands split across a backslash
    continuation, with the path and the pipe on the line AFTER the reader
    (`D07.3:108`, `D08.2:265/307/322`, `D05.2:501`, `D05.3:302`, `D09.2:113`,
    `D04.1:148`, `D10.5:239`). The other fourteen are PROSE explaining this very
    defect — "it named `stackowl.jsonl`, one file" — which a line matcher would
    have flagged as the defect it describes. Joining finds the nine; the fence
    walk drops the fourteen. Neither cure alone is enough.
    """
    lines = text.splitlines()
    out: list[tuple[int, str]] = []
    i = 0
    while i < len(lines):
        if not lines[i].lstrip().startswith("```"):
            i += 1
            continue
        close = i + 1
        while close < len(lines) and not lines[close].lstrip().startswith("```"):
            close += 1
        for offset, cmd in _fence_commands(lines[i + 1:close]):
            if _ANY_LOG.search(cmd):
                out.append((i + 2 + offset, cmd.strip()))
        i = close + 1
    return out


def _single_log_queries(text: str) -> list[tuple[int, str]]:
    """(line_no, command) for every Verification command pinned to the single log.

    Commands carrying :data:`_ON_PURPOSE` are excluded here and counted separately
    by the caller, so "deliberate" is visible in the report rather than absent.
    """
    return [
        (n, cmd) for n, cmd in _fenced_log_commands(text)
        if "stackowl*.jsonl" not in cmd
        and _ON_PURPOSE not in cmd
        and _SINGLE_LOG.search(cmd)
    ]


def _on_purpose_queries(text: str) -> int:
    """How many single-file queries the author explicitly marked as current-boot."""
    return sum(
        1 for _n, cmd in _fenced_log_commands(text)
        if _ON_PURPOSE in cmd and _SINGLE_LOG.search(cmd)
    )


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


#: A log file, named. RETIRED `_LOG_QUERY`, which required one of five reader words on
#: the same joined command as the path — a CO-OCCURRENCE standing in for a single token,
#: which is the prose-whitelist defect wearing a structural disguise: the regex LOOKS
#: like structure. MEASURED 2026-09-07: dropping the reader word raises the examined
#: denominator from 36 dated blocks to 38 and changes NO verdict, which is what a
#: coverage fix should look like. The 14 commands it could not see are quote-wrapped
#: `jq` programs and two `glob.glob(...)` Python readers — a `.jsonl` under
#: `~/.stackowl/logs` inside a fence is a log read, whatever spells the reading.
_LOG_PATH = re.compile(r"stackowl[^\s'\"]*\.jsonl")

#: A date recorded beside one. NO KEYWORD GATE — see `_evidence_older_than_the_logs`
#: for why the six-word whitelist this replaces saw 24 of 43.
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")

#: How far past a fenced block's close its result may be written. A block's reading is
#: often a heading or a table UNDER it rather than a comment inside it — D05.8's is
#: `### Live reading, 2026-08-22`, two lines below the closing fence — so the window has
#: to leave the fence. It stops at the next fence regardless, whatever this number says.
#:
#: EIGHT IS THE TOP OF A MEASURED PLATEAU, not a taste. Swept over the live corpus, the
#: answer is INVARIANT for 2..8 (35 dated blocks, 1 rotted) and wrong on both sides:
#: 0 and 1 lose the only real defect, whose heading sits two lines below its fence; 12
#: gives 2, 16 gives 3, and letting it run to the next fence — the shape a review
#: recommended, on the reasoning that a positional bound needs no number — gives NINE,
#: of which EIGHT are false. Those eight are the same bleed this detector was just fixed
#: for, arriving from the other end: `*fixed 2026-08-14*` in a table of BUGS, and five
#: hits on `**Every command below has been RUN, on 2026-07-28**` — a heading that
#: belongs to the NEXT block and says so in its own words. An unbounded window does not
#: read a block's evidence; it reads its neighbour's introduction.
_TRAILING_PROSE_LINES = 8


def _fence_commands(body: list[str]) -> list[tuple[int, str]]:
    """(offset into `body`, joined command) for every runnable line in one fence.

    Backslash continuations are joined, which is not a nicety: MEASURED 2026-09-07,
    FIFTEEN fenced blocks in this corpus name a `.jsonl` log and are invisible to a
    per-line matcher, because the reader sits on one line and the glob on the next.
    Three of the five commands in the block carrying the ONE real finding are of that
    shape. `_newest_by_pipe_position` already joins them for the same reason; this is
    that idiom, factored so the two cannot drift apart. Joining raised the examined
    denominator from 31 blocks to 35 and left the finding unchanged — coverage, not a
    new verdict.

    `#` comment lines are not commands. They are still read for DATES by the caller;
    they are just not the thing a reader executes.
    """
    out: list[tuple[int, str]] = []
    pending: list[str] = []
    start = 0
    for n, line in enumerate(body):
        if line.lstrip().startswith("#"):
            pending = []
            continue
        if not pending:
            start = n
        pending.append(line.rstrip())
        if line.rstrip().endswith("\\"):
            continue
        out.append((start, " ".join(x.rstrip("\\") for x in pending)))
        pending = []
    return out


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


#: A database query a reader cannot run: the CLI this repo does not depend on, or the
#: zero-byte stray that sits where the database looks like it should be.
#: `sqlite3` followed by something a shell would treat as an ARGUMENT — a quote, a path,
#: a flag, a variable. Prose says "sqlite3 is NOT installed on this box", and the first
#: version of this detector flagged exactly that line inside D01.2's fence: the note
#: CORRECTING this defect, reported as an instance of it. Third time a detector in this
#: file has read its own explanation, and scoping to fenced non-comment lines does not
#: help when the prose is bare inside a fence. Requiring an argument shape excludes every
#: English sentence by construction rather than by a word list.
_ABSENT_SQLITE = re.compile(r"""(?<![\w.-])sqlite3\s+(?=["'~./$-]|\S*\.db\b)""")
_STRAY_DB = re.compile(r"~/\.stackowl/stackowl\.db")


def _cannot_query_the_database(text: str) -> list[tuple[int, str, str]]:
    """(line_no, command, why) for a Verification command that cannot reach the database.

    TWO FACTS ABOUT THIS BOX WERE MEASURED, WRITTEN DOWN, AND THEN IGNORED. PROCESS.md's
    "Evidence, not assertion" section already records both — the `sqlite3` CLI is not
    installed here, and the live database is `<workspace>/stackowl.db` while
    `~/.stackowl/stackowl.db` is a ZERO-BYTE stray from 2026-07-25 that still sits there
    looking canonical. It records them as the worked example of a document that would
    have failed its own Verification section.

    MEASURED 2026-09-07, three days after that section was last edited: ELEVEN
    Verification commands across six design documents still invoked `sqlite3`, and
    ESC-73's acceptance check named BOTH wrong things at once — `sqlite3 stackowl.db` —
    so it could not have closed on any database content since it was written on
    2026-08-31. The rule lived in the method document and nothing enforced it, which is
    the same decay `premise_check` fixed for escalations and `closing_check` fixed for
    partial stages. This is the fourth instance of one cure.

    AND THE FAILURE IS SILENT BY CONSTRUCTION, which is why none of the eleven was ever
    noticed: `command not found` goes to stderr and nothing goes to stdout, so a check
    written as `… | wc -l` reads 0 — and 0 reads as *not yet*, never as *wrong
    instrument*. The stray path makes the identical shape from the other direction: a
    real file, a successful open, and no rows in it, ever.

    The cure is `scripts/db_query.sh`, which resolves the path from `StackowlHome` and
    EXITS NON-ZERO on a missing or empty database rather than returning an empty result
    set. Flagging `sqlite3` is a repo policy, not a probe of `$PATH`: this tree queries
    SQLite through the module everywhere in `src/`, so a document reaching for the CLI is
    reaching for a dependency the project does not have.
    """
    out: list[tuple[int, str, str]] = []
    fenced = False
    for i, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if not fenced or line.lstrip().startswith("#"):
            continue
        why = ""
        if _ABSENT_SQLITE.search(line):
            why = "invokes the `sqlite3` CLI — not a dependency of this tree"
        elif _STRAY_DB.search(line):
            why = "names ~/.stackowl/stackowl.db — the zero-byte stray, not the live db"
        if why:
            out.append((i, line.strip(), why))
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


def _evidence_older_than_the_logs(
    text: str, horizon: str
) -> list[tuple[int, str, str, bool]]:
    """(first command line, newest recorded date, that command, rotted) per dated BLOCK.

    A Verification command that reads the logs and records "PASS 2026-08-21: 8
    occurrences" is a CHECK for as long as the logs reach back that far, and a RECORD
    afterwards. Once the evidence rotates away the command returns 0 forever, and 0 reads
    as failure to anyone who runs it — the ambiguous-zero trap with a cause of its own:
    THE EVIDENCE HAD A SHORTER LIFETIME THAN THE DOCUMENT.

    A fresher date anywhere in the same block clears it, which is not a nicety: D08.1
    carries "RAN 2026-08-17 -> 4 firings" with "RE-RAN 2026-09-07 -> 99" on the next
    line, and flagging that would be crying wolf on a document that had already done the
    work.

    TWO DEFECTS WERE MEASURED IN THE FIRST VERSION OF THIS, 2026-09-07 — one loop after
    the report was drained to zero and reported as drained. It was reading zero because
    it was BLIND, and nothing in the output could tell the two apart.

    ONE — IT ASKED PROSE FOR A DATE THAT IS ALREADY A STRUCTURED TOKEN. It matched
    ``(PASS|MEASURED|RAN|RE-RAN|Measured|Re-ran)\\s+<date>``: a hand-written whitelist of
    six words over prose that is unbounded by construction. Across 84 documents it saw a
    date beside 24 of the 43 log-query windows that carry one. The phrasings it missed
    are not exotic — ``PASS (observed 2026-07-27)``, ``### Live reading, 2026-08-22``,
    ``RAN: 201,``, ``All three RUN on 2026-09-04``, and ``RE-RUN`` itself, which is not
    ``RE-RAN``. This is the SAME cause ``_test_paths_on_command_lines`` rejected one loop
    earlier for negation, in the mirror: a regex over prose is a guess at future
    phrasing, and the lesson was learned in one detector and not carried to its sibling
    twenty lines away. The cure is the same — read the STRUCTURE. Every ISO date in the
    block counts now, and ``max`` still clears, so a re-run written underneath an old
    result silences it exactly as before.

    TWO — THE WINDOW WAS TEN LINES AND BLED INTO THE NEXT COMMAND'S ANNOTATIONS. With
    every date counting, D01.7 step 8 was flagged on step 9's ``PASS (observed
    2026-07-27)`` — a different command, in a different fence, whose own ``RE-RUN
    2026-09-07`` sat one line past the window's end. **Evidence attaches to a BLOCK, not
    to a LINE.** A fenced run of commands is ONE act of measurement and its result is
    written under it, so the window is the whole fence plus the prose that follows, and
    it stops at the next fence. That is also what keeps the one real defect: D05.8's
    reading is a heading two lines BELOW the closing fence, which no in-fence window
    could ever have seen. Widening the pattern without also widening the scope would
    have traded one blind spot for one false positive.

    MEASURED with both in place: 60 blocks carry a log query, 31 carry a date, and ONE
    is older than the horizon. Every block that carries a date is returned — the caller
    needs the denominator, because a clean corpus and a blind detector print the same
    empty report otherwise, which is how this defect survived a loop.
    """
    if not horizon:
        return []
    lines = text.splitlines()
    out: list[tuple[int, str, str, bool]] = []
    i = 0
    while i < len(lines):
        if not lines[i].lstrip().startswith("```"):
            i += 1
            continue
        # ONLY INSIDE A FENCE. A Verification command is one a reader would RUN; a
        # command quoted in prose is a description of one. The distinction is not
        # pedantic — the document that FIXES this defect has to quote the retired
        # command to explain it, and a line-scoped detector flagged that correction as
        # the defect. Same shape as the escalation report reading its own explanation.
        close = i + 1
        while close < len(lines) and not lines[close].lstrip().startswith("```"):
            close += 1
        body = lines[i + 1:close]
        commands = [
            (i + 2 + offset, cmd)
            for offset, cmd in _fence_commands(body)
            if _LOG_PATH.search(cmd)
        ]
        if commands:
            trailing: list[str] = []
            k = close + 1
            while (k < len(lines) and len(trailing) < _TRAILING_PROSE_LINES
                   and not lines[k].lstrip().startswith("```")):
                trailing.append(lines[k])
                k += 1
            dates = _ISO_DATE.findall("\n".join(body + trailing))
            if dates:
                newest = max(dates)
                out.append(
                    (commands[0][0], newest, commands[0][1].strip(), newest < horizon)
                )
        i = close + 1
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


#: How many rows each section prints before it truncates.
#:
#: WHY IT IS A VARIABLE. Every list here was hard-capped at 12, so the report
#: named 12 of 31 BLIND AFTER MIDNIGHT commands and hid the other 19 behind
#: "… and 19 more". A finding you cannot enumerate cannot be worked: the report
#: could say the corpus was dirty and could not say WHICH lines to fix, which
#: made the fix impossible to do FROM THE REPORT — the instrument's own version
#: of a count without its contents. `--all` prints every row.
_LIST_CAP = 12


def main(argv: list[str] | None = None) -> int:
    global _LIST_CAP
    args = sys.argv[1:] if argv is None else argv
    if "--all" in args:
        _LIST_CAP = 10**9
    docs = sorted(_DESIGNS.glob("*.md"))
    stale: list[tuple[str, str, str, list[str], list[str]]] = []
    fresh = 0
    unmeasurable: list[tuple[str, str]] = []
    reviewed_only: list[tuple[str, str, int]] = []
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
        changes = _changes_since(real, date.group(1))
        reviewed = _reviewed_shas(head)
        unreviewed = [c for c in changes if c[1][:8] not in reviewed]
        if changes and not unreviewed:
            # READ AND DISMISSED, which is neither stale nor freshly verified. It
            # gets its own line because folding it into `fresh` would hide the one
            # thing worth auditing: how much of this corpus is standing on a
            # judgement rather than on a re-run.
            reviewed_only.append((doc.name, date.group(1), len(changes)))
        elif unreviewed:
            stale.append((
                doc.name, date.group(1), max(c[0] for c in unreviewed),
                sorted({f for c in unreviewed for f in c[3]}),
                [f"{c[1]} {c[2]}" for c in unreviewed],
            ))
            if deleted := _deletions_since(real, date.group(1)):
                by_deletion.append((doc.name, deleted))
        else:
            fresh += 1

    print(f"design documents: {len(docs)}\n")
    if stale:
        print(f"STALE — sources changed after the document was verified ({len(stale)}):")
        for name, verified, changed, culprits, subjects in sorted(stale, key=lambda r: r[1]):
            who = ", ".join(c.split("/")[-1] for c in culprits) or "?"
            print(f"  {name:16} verified {verified}   sources changed {changed}"
                  f"   <- {who}")
            for subject in subjects[:_LIST_CAP]:
                print(f"       {subject}")
            if len(subjects) > _LIST_CAP:
                print(f"       … and {len(subjects) - _LIST_CAP} more")
        # A SOURCE CITED BY MANY DOCUMENTS IS A NOISE SOURCE, and worth naming as
        # one: `startup/orchestrator.py` is 4,839 lines and changes about twice a
        # day, so it marks every document citing it stale whether or not the
        # change concerned them. Without this line a reader re-reads N documents
        # to discover they share one irrelevant cause.
        blamed: dict[str, int] = {}
        for _n, _v, _c, culprits, _w in stale:
            for c in culprits:
                blamed[c] = blamed.get(c, 0) + 1
        shared = {c: n for c, n in blamed.items() if n > 1}
        if shared:
            worst = ", ".join(
                f"{c.split('/')[-1]} ({n} documents)"
                for c, n in sorted(shared.items(), key=lambda kv: -kv[1])
            )
            print(f"  ONE SOURCE, MANY DOCUMENTS: {worst} — check whether the change "
                  f"even concerned them before re-reading each one.")
        print()

    if reviewed_only:
        print(f"REVIEWED, NOT RE-RUN — every change since is recorded as examined "
              f"({len(reviewed_only)}):")
        for name, verified, n in sorted(reviewed_only, key=lambda r: r[1]):
            print(f"  {name:16} verified {verified}   {n} commit(s) read and dismissed")
        print("  These stand on a JUDGEMENT, not on a re-run. That is the honest "
              "answer to an unrelated commit, and it is also where a wrong one hides.")
        print()
    single_log: list[tuple[str, int, str]] = []
    on_purpose = 0
    log_commands = 0
    for doc in docs:
        body = doc.read_text(encoding="utf-8")
        log_commands += len(_log_query_lines(body))
        for ln, line in _single_log_queries(body):
            single_log.append((doc.name, ln, line))
        on_purpose += _on_purpose_queries(body)

    if by_deletion:
        print(f"STALE BY DELETION — read these first ({len(by_deletion)} of "
              f"{len(stale)}). A source these documents declare was SUBTRACTED, so "
              "each may describe something that no longer exists:")
        for name, commits in sorted(by_deletion):
            print(f"  {name}")
            for line in commits[:3]:
                print(f"        {line}")
        print()
    print(f"checked {fresh + len(stale) + len(reviewed_only)}, STALE {len(stale)} "
          f"({len(by_deletion)} by deletion), reviewed-not-rerun {len(reviewed_only)}, "
          f"unmeasurable {len(unmeasurable)}")
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
        for name, ln, line in single_log[:_LIST_CAP]:
            print(f"  {name}:{ln}  {line[:88]}")
        if len(single_log) > _LIST_CAP:
            print(f"  … and {len(single_log) - _LIST_CAP} more")
        if on_purpose:
            print(f"  ({on_purpose} further single-file quer(ies) are marked "
                  f"`{_ON_PURPOSE}` and excluded — deliberately scoped to one boot, "
                  f"where the glob would be WRONG.)")
    elif log_commands:
        print(f"\nBLIND AFTER MIDNIGHT — none, across {log_commands} log-reading "
              f"Verification command(s); every one globs, or is one of {on_purpose} "
              f"marked `{_ON_PURPOSE}` because it reads ONE boot on purpose and the "
              "glob would be wrong. (The denominator is printed because a silent "
              "detector and a clean corpus look identical.)")

    stages = _validate_stages()
    untracked: list[tuple[str, int, str]] = []
    open_markers = 0
    staged_docs = 0
    for doc in docs:
        body = doc.read_text(encoding="utf-8")
        open_markers += len(_open_acceptance_lines(body))
        stage = stages.get(doc.stem)
        if stage is not None:
            staged_docs += 1
        if stage != "done":
            continue
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
        for name, ln, line in untracked[:_LIST_CAP]:
            marker, _, note = line.partition("\n")
            print(f"  {name}:{ln}  {marker[:88]}")
            print(f"      {note.strip()}")
        if len(untracked) > _LIST_CAP:
            print(f"  … and {len(untracked) - _LIST_CAP} more")
    elif open_markers or staged_docs:
        print(f"\nOPEN BUT NOT TRACKED — none, across {open_markers} OPEN acceptance "
              f"marker(s); {staged_docs} of {len(docs)} document(s) matched a stage "
              "entry in `progress.yml`. (Both numbers are printed because this report "
              "goes silent in TWO ways — a clean corpus, and a corpus whose stages it "
              "could not resolve at all. A `staged_docs` of 0 means blind, not clean.)")

    horizon = _log_horizon()
    rotted: list[tuple[str, int, str, str]] = []
    dated_blocks = 0
    for doc in docs:
        for ln, when, cmd, is_rotted in _evidence_older_than_the_logs(
            doc.read_text(encoding="utf-8"), horizon
        ):
            dated_blocks += 1
            if is_rotted:
                rotted.append((doc.name, ln, when, cmd))
    if rotted:
        print(f"\nEVIDENCE OLDER THAN THE LOGS — {len(rotted)} Verification command(s) "
              f"in {len({r[0] for r in rotted})} document(s) record a result from BEFORE "
              f"the oldest retained log ({horizon}). Re-running them cannot reproduce "
              "what they claim: they return 0, and 0 reads as failure. These are RECORDS "
              "now, not checks — date them as such, and give the reader something "
              "runnable beside them:")
        for name, ln, when, cmd in rotted[:_LIST_CAP]:
            print(f"  {name}:{ln}  recorded {when}")
            print(f"      {cmd[:88]}")
    elif dated_blocks:
        print(f"\nEVIDENCE OLDER THAN THE LOGS — none, across {dated_blocks} dated "
              f"evidence block(s), against a log horizon of {horizon}. (The denominator "
              "is printed because this report read a silent ZERO for a whole loop while "
              "its finder was blind, and nothing in the output could say which.)")

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
        for name, ln, cmd in unordered[:_LIST_CAP]:
            print(f"  {name}:{ln}  {cmd[:96]}")
        if len(unordered) > _LIST_CAP:
            print(f"  … and {len(unordered) - _LIST_CAP} more")
    elif by_position:
        print(f"\nNEWEST RECORD TAKEN BY PIPE POSITION — none, across {by_position} "
              "command(s) that take a record by position from the multi-file glob; every "
              "one of them sorts first. (The denominator is printed because a silent "
              "detector and a clean corpus look identical.)")

    unreachable: list[tuple[str, int, str, str]] = []
    db_queries = sum(
        doc.read_text(encoding="utf-8").count("db_query.sh") for doc in docs
    )
    for doc in docs:
        for ln, cmd, why in _cannot_query_the_database(doc.read_text(encoding="utf-8")):
            unreachable.append((doc.name, ln, cmd, why))
    if unreachable:
        print(f"\nCANNOT QUERY THE DATABASE — {len(unreachable)} Verification command(s) "
              f"in {len({u[0] for u in unreachable})} document(s) reach for the `sqlite3` "
              "CLI this tree does not depend on, or for the zero-byte stray at "
              "~/.stackowl/stackowl.db. Both fail SILENTLY into an empty stdout, so a "
              "check reading `| wc -l` returns 0 and 0 reads as *not yet*. Use "
              "`./scripts/db_query.sh '<SQL>'`, which resolves the path from StackowlHome "
              "and exits non-zero on a missing or empty database:")
        for name, ln, cmd, why in unreachable[:_LIST_CAP]:
            print(f"  {name}:{ln}  {cmd[:88]}")
            print(f"      {why}")
        if len(unreachable) > _LIST_CAP:
            print(f"  … and {len(unreachable) - _LIST_CAP} more")
    elif db_queries:
        print(f"\nCANNOT QUERY THE DATABASE — none, across {db_queries} database "
              "query/queries on runnable Verification commands; every one goes through "
              "`scripts/db_query.sh`. (The denominator is printed because a silent "
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
        for name, ln, rel, line in gone[:_LIST_CAP]:
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
