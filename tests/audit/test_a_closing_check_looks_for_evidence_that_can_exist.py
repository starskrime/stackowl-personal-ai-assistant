"""A closing check that returns 0 must mean "not yet", never "impossible".

WHY THIS EXISTS, and it is my own defect from earlier the same day.

`validate_check.py` re-runs each partial stage's `closing_check` and prints OPEN or
CLOSEABLE. **A zero is ambiguous.** It can mean the event has not happened yet — which is
what OPEN is for — or that the evidence being asked for cannot ever appear, which is not an
open question at all but a dead end wearing one's clothes. Nothing distinguished them.

MEASURED 2026-09-06. D14.4's check greps the logs for `health sweep found unhealthy`. That
string exists in `health_sweep.py:489` — as the first line of the message `_compose_alert`
RETURNS. It is handed to the outbound alert sink and to `JobResult.output`, and it is never
passed to a logger. So:

  * the sweep detected unhealthy subsystems **735 times**, 3 of them that day;
  * the string appears in the logs **zero times, ever**;
  * and it never can, because nothing logs it.

That check would have read OPEN forever. It is exactly the D11.3 defect — evidence that
cannot exist — committed the same day the rule against it was written into the skill, and
the reason is worth naming: I ran the query, got 0, and read the 0 as "hasn't happened yet"
without asking whether it could ever be anything else.

THE DISTINGUISHING PROPERTY IS CHECKABLE. A log-based check can only ever fire if the
pattern it greps for is a string some `log.*.info/warning/error(...)` call actually emits.
A string that is merely PRESENT in the tree — returned, stored, rendered to a user — proves
nothing about the logs. So this guard asks the loggers, not the file.

It also enforces the level: production runs at INFO, so a pattern emitted only at DEBUG is
the D08.1 failure, which this programme has already paid for twice.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src" / "stackowl"

sys.path.insert(0, str(_ROOT / "scripts"))
from progress_lint import entries_with_closing_checks as _record_checks  # noqa: E402

#: Levels that exist in production. DEBUG does not.
_PRODUCTION_LEVELS = {"info", "warning", "error", "critical", "exception"}

#: Patterns whose emitter this guard cannot SEE, with the proof that it exists anyway.
#:
#: The AST walk reads message literals at the logging call. It cannot follow a message
#: assembled from a value passed in elsewhere — and one real case does exactly that:
#: `TurnNudge` logs `f"{self.label}: due"`, so the label its caller supplied never appears
#: as a literal at the call site.
#:
#: Declared rather than inferred, and each entry must cite EVIDENCE that the string really
#: does reach the logs. A blanket "allow indirection" would defeat the guard; naming the
#: two-line reason keeps it honest and makes a third one a decision.
_INDIRECT_EMITTERS = {
    '"level": "ERROR"': (
        "A FORMATTER FIELD, not a message. `JsonlFormatter` writes `level` on EVERY "
        "record (infra/observability.py), so no `log.*` call site can carry it as a "
        "literal and the AST walk cannot see it by construction. PROOF it reaches the "
        "logs: 3,630 lines match it on 2026-09-07 alone. Declared because DEBT-203 has to "
        "ask 'ERROR lines that are not the provider family', and severity is only "
        "expressible as the field the formatter emits."
    ),
    '"level": "CRITICAL"': (
        "The same formatter field at the other severity — written by `JsonlFormatter` on "
        "every record, so no `log.*` literal can carry it and the AST walk cannot see it. "
        "PROOF it reaches the logs: 1 line matches on 2026-09-07, "
        "'[loop] CRITICAL — the durable task loop has failed every tick'. It is listed "
        "SEPARATELY rather than folded into a regex with ERROR because that one line would "
        "otherwise be invisible inside a four-figure count."
    ),
    "[skills] nudge": (
        "TurnNudge logs f\"{self.label}: due\" (infra/nudge.py:92) and the label is passed "
        "at skills/nudge.py:46. PROOF the indirection reaches the logs: the SIBLING label "
        "\"[curated] nudge\" (memory/curated.py:1135) runs the identical code path and has "
        "produced 98 log lines."
    ),
}


@lru_cache(maxsize=1)
def _logged_literals() -> tuple[tuple[str, str], ...]:
    """Every (message-literal, level) a logging call in `src/` can emit.

    Collected from the AST rather than by grepping, because a grep cannot tell a string
    that is LOGGED from one that is returned, stored, or rendered — which is the entire
    distinction this file exists to make. f-strings contribute their literal fragments,
    since a pattern is usually matched against the fixed part.
    """
    out: list[tuple[str, str]] = []
    for path in _SRC.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            level = node.func.attr
            if level not in _PRODUCTION_LEVELS | {"debug"} or not node.args:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                out.append((first.value, level))
            elif isinstance(first, ast.JoinedStr):
                for part in first.values:
                    if isinstance(part, ast.Constant) and isinstance(part.value, str):
                        out.append((part.value, level))
    return tuple(out)


def _log_patterns(check: str) -> list[str]:
    """The log patterns a closing check greps for, with regex escaping removed.

    Only `log_since.sh` calls count — a check that reads the database or the tree is not
    making a claim about the logs at all.
    """
    code = "\n".join(
        ln for ln in check.splitlines() if not ln.lstrip().startswith("#")
    )
    pats = re.findall(r"log_since\.sh\s+\S+\s+'([^']+)'", code)
    return [p.replace("\\[", "[").replace("\\]", "]").replace("\\", "") for p in pats]


def _raw_log_patterns(check: str) -> list[str]:
    """The patterns EXACTLY as the shell receives them — backslashes intact.

    `_log_patterns` unescapes, because it compares against logger message literals
    where `\\[` and `[` mean the same character. That normalisation is right there and
    catastrophic here: it ERASES the difference between a correctly escaped pattern and
    a broken one, which is precisely the difference `grep` cares about.
    """
    code = "\n".join(
        ln for ln in check.splitlines() if not ln.lstrip().startswith("#")
    )
    return re.findall(r"log_since\.sh\s+\S+\s+'([^']+)'", code)


def _log_bounds(check: str) -> list[str]:
    """The date bounds a closing check passes to `log_since.sh`."""
    code = "\n".join(
        ln for ln in check.splitlines() if not ln.lstrip().startswith("#")
    )
    return re.findall(r"log_since\.sh\s+(\d{4}-\d{2}-\d{2})", code)


def _emitters(pattern: str) -> list[tuple[str, str]]:
    """Logging calls whose message could contain *pattern*."""
    needle = pattern.split(".*")[0].strip()
    return [(m, lvl) for m, lvl in _logged_literals() if needle and needle in m]


def _checks() -> list[tuple[str, str]]:
    """Every closing check in the record — items AND known_debt.

    Asks `progress_lint.entries_with_closing_checks` rather than iterating `items`
    itself. This used to be a private loop over `items`, which meant a check written
    against evidence-led work in `known_debt` escaped every guard in this file: the
    one place a new check would be least reviewed was the one place nothing looked.
    """
    data = yaml.safe_load((_ROOT / "progress.yml").read_text(encoding="utf-8"))
    return _record_checks(data)


class TestEveryLogPatternHasAnEmitter:
    @pytest.mark.tripwire
    def test_no_check_waits_for_a_string_nothing_logs(self) -> None:
        """THE DEAD-END DETECTOR. A pattern with no logging call behind it can never
        appear, so its check reads OPEN forever and the item can never advance."""
        impossible: dict[str, str] = {}
        for item_id, check in _checks():
            for pattern in _log_patterns(check):
                if pattern in _INDIRECT_EMITTERS:
                    continue
                if not _emitters(pattern):
                    impossible[f"{item_id}: {pattern!r}"] = (
                        "no logging call in src/ emits this"
                    )

        assert not impossible, (
            "these closing checks wait for evidence that cannot appear — nothing logs "
            f"the string they grep for, so they read OPEN forever:\n  {impossible}"
        )

    @pytest.mark.tripwire
    def test_no_check_waits_for_a_DEBUG_only_string(self) -> None:
        """Production runs at INFO. A pattern emitted only at DEBUG is the D08.1
        failure: an acceptance check no volume of traffic could ever close."""
        debug_only: dict[str, str] = {}
        for item_id, check in _checks():
            for pattern in _log_patterns(check):
                levels = {lvl for _m, lvl in _emitters(pattern)}
                if levels and not (levels & _PRODUCTION_LEVELS):
                    debug_only[f"{item_id}: {pattern!r}"] = f"only at {sorted(levels)}"

        assert not debug_only, (
            f"these closing checks grep for a DEBUG-only line: {debug_only}"
        )

    def test_the_guard_sees_a_real_population(self) -> None:
        """VACUITY CONTROL. If the AST walk returned nothing, or no check parsed, both
        assertions above would pass by measuring an empty set."""
        assert len(_logged_literals()) >= 500, len(_logged_literals())
        assert sum(len(_log_patterns(c)) for _i, c in _checks()) >= 4


class TestTheGuardCanTellTheTwoApart:
    """The control that makes the assertions above mean something: it must accept a
    string that IS logged and reject one that merely exists in the tree."""

    def test_it_accepts_a_string_a_logger_emits(self) -> None:
        assert _emitters("[scheduler] health_sweep: bound to the live"), (
            "a known INFO line was not found — the AST walk is broken"
        )

    def test_it_rejects_a_string_that_is_only_RETURNED(self) -> None:
        """`_compose_alert` builds this and hands it to the alert sink; no logger ever
        sees it. This is the exact string D14.4's first closing check waited on."""
        assert not _emitters("health sweep found unhealthy"), (
            "the guard cannot tell a logged string from a returned one, which is the "
            "only distinction it exists to make"
        )


class TestTheExemptionListIsItselfHonest:
    """An exemption list is a place for defects to hide. These keep it small and true."""

    def test_every_exemption_cites_its_evidence(self) -> None:
        for pattern, reason in _INDIRECT_EMITTERS.items():
            assert "PROOF" in reason, f"{pattern!r} is exempt without stating why"
            assert len(reason) > 120, f"{pattern!r} has a one-line excuse, not a reason"

    def test_no_exemption_is_stale(self) -> None:
        """If a pattern gains a direct emitter, the exemption should go — otherwise the
        list grows into a place nobody re-reads."""
        stale = [p for p in _INDIRECT_EMITTERS if _emitters(p)]

        assert not stale, f"these now have a direct emitter and need no exemption: {stale}"

    def test_the_exemption_is_still_needed(self) -> None:
        """The control in the other direction: if this passes trivially because no check
        uses the pattern any more, the entry is dead weight."""
        used = {p for _i, c in _checks() for p in _log_patterns(c)}

        assert set(_INDIRECT_EMITTERS) <= used, (
            f"exempt patterns no closing check uses: {set(_INDIRECT_EMITTERS) - used}"
        )


class TestAPatternMatchesTheTextItIsAPatternFor:
    """THE INSTRUMENT'S OWN METACHARACTERS, and this programme has now paid for them
    three times.

    `"msg": "` needs the SPACE after the colon or a regex returns empty against a 14MB
    file. In SQL `LIKE`, `_` is a WILDCARD, and `skill_name LIKE 'incident_%'` matched
    `incident-evidence-brief`. And MEASURED 2026-09-06, the third: D09.4's closing check
    grepped for `[skills] nudge`, where `[...]` is a CHARACTER CLASS. It matches one
    character from {s,k,i,l} followed by " nudge"; the real line is `[skills] nudge: due`,
    whose preceding character is `]`. Verified against a file containing exactly that
    line: the check's own pattern returns 0, the escaped form returns 1.

    So the check could never close. Not because the evidence cannot exist — the sibling
    `[curated] nudge` has 99 lines — but because the instrument could not SEE it. That is
    one layer below the failure `TestEveryLogPatternHasAnEmitter` above was built for.

    AND THE GUARD ABOVE EXEMPTED IT. `_INDIRECT_EMITTERS` carries `[skills] nudge`
    because the AST cannot follow a label passed in by a caller — a true and
    well-evidenced reason. But an exemption granted for reason A silently exempts from
    reasons B, C and D as well, and nothing then asked whether the pattern was even
    well-formed. That is the general shape worth keeping: an exemption is a hole the
    exact width of every check it skips, not the one it was written for.

    THE RULE NEEDS NO LOGGER, which is what makes it cover the exempt patterns too: a
    pattern must match the literal text it is a pattern FOR. Unescaping a pattern yields
    the text its author meant to find, so `re.search(raw, unescaped)` must hold. It
    catches `[`, `(`, `?`, `+` and `{` alike, rather than one metacharacter at a time.
    """

    @pytest.mark.tripwire
    def test_every_pattern_matches_its_own_literal_text(self) -> None:
        broken: dict[str, str] = {}
        for item_id, check in _checks():
            for raw in _raw_log_patterns(check):
                literal = (
                    raw.replace("\\[", "[").replace("\\]", "]").replace("\\", "")
                )
                try:
                    if not re.search(raw, literal):
                        broken[f"{item_id}: {raw!r}"] = (
                            f"does not match the text it targets ({literal!r}) — a "
                            "metacharacter is being read as syntax"
                        )
                except re.error as exc:
                    broken[f"{item_id}: {raw!r}"] = f"is not a valid regex: {exc}"

        assert not broken, (
            "these closing checks grep for a pattern that cannot match the line they "
            f"name, so they read OPEN whatever production does:\n  {broken}"
        )

    def test_the_rule_sees_a_real_population(self) -> None:
        """VACUITY CONTROL. If no raw pattern parsed, the assertion passes over
        nothing — the failure mode this whole file exists to prevent."""
        raws = [r for _i, c in _checks() for r in _raw_log_patterns(c)]

        assert len(raws) >= 4, f"only parsed {len(raws)} raw patterns"
        assert any("\\[" in r for r in raws), (
            "no pattern escapes a bracket any more; either the corpus changed or the "
            "raw reader has started unescaping, which is the defect itself"
        )

    def test_the_rule_would_catch_the_defect_it_was_written_for(self) -> None:
        """The control in the other direction: a rule that cannot fail is decoration."""
        assert not re.search("[skills] nudge", "[skills] nudge")
        assert re.search(r"\[skills\] nudge", "[skills] nudge")


#: A grep invocation in a document's Verification section.
_DOC_GREP = re.compile(r"grep(?:\s+-[\w-]+)*\s+(['\"])(.+?)\1")
#: A bracketed LOG PREFIX — `[paths]`, `[memory]` — left unescaped, so grep reads it as
#: a character class. Deliberately narrow; see the class docstring for why.
_UNESCAPED_PREFIX = re.compile(r"(?<!\\)\[[a-z_]+\](?=\s)")


class TestADocumentsVerificationCommandCanActuallyRun:
    r"""The same cause, one population over — which is the question this programme
    requires be answered before an item is done.

    A design document's Verification section is the evidence behind its `Last verified`
    stamp, and those commands are greps too. MEASURED 2026-09-06: THREE carried an
    unescaped bracketed log prefix, in D05.1 (twice) and D09.2. Proven against a file
    containing the exact lines: each returned 0 unescaped and 1 escaped.

    Both claims turned out to be TRUE — re-run with working patterns, D05.1's line has
    195 occurrences and D09.2's 23 — so nothing in those documents was wrong except the
    instrument that was supposed to demonstrate it. D05.1's was wrong twice over: it
    also said "model-writable root" where the message says "workspace".

    THIS RULE IS NARROWER THAN THE ONE ABOVE, on purpose. Applying the closing-check
    rule — a pattern must match its own literal text — to this corpus reports 14
    failures, and most are correct: documents use grep as a genuine regex tool, and
    `^goal-\|^incident-`, `launched [0-9]+ (durable-task|pending-message)` and
    `log\.(info|debug)\(` are all deliberate. A guard that cries wolf on correct work
    is the failure this programme keeps paying for, so this asks only about the one
    shape that is never intentional: a bracketed log prefix left unescaped.
    """

    @pytest.mark.tripwire
    def test_no_verification_grep_reads_a_log_prefix_as_a_character_class(self) -> None:
        docs = _ROOT / "docs" / "reference-mapping" / "designs"
        broken: dict[str, str] = {}
        for doc in sorted(docs.glob("*.md")):
            for match in _DOC_GREP.finditer(doc.read_text(encoding="utf-8")):
                pattern = match.group(2)
                found = _UNESCAPED_PREFIX.search(pattern)
                if found:
                    broken[f"{doc.name}: {pattern[:60]}"] = (
                        f"{found.group(0)} is a character class, not a prefix"
                    )

        assert not broken, (
            "these Verification commands cannot match the line they name, so the "
            f"`Last verified` stamp above them rests on a grep returning 0:\n  {broken}"
        )

    def test_the_doc_sweep_sees_a_real_population(self) -> None:
        """VACUITY CONTROL. A regex that stopped finding grep invocations would make
        the assertion above pass over an empty corpus."""
        docs = _ROOT / "docs" / "reference-mapping" / "designs"
        greps = [
            m.group(2)
            for d in docs.glob("*.md")
            for m in _DOC_GREP.finditer(d.read_text(encoding="utf-8"))
        ]

        assert len(greps) >= 50, f"only found {len(greps)} grep patterns in the corpus"
        assert any("\\[" in g for g in greps), (
            "no document escapes a bracket any more — either the corpus changed or "
            "this sweep has stopped seeing the shape it exists to check"
        )


class TestTheSingleLogFileReportSeesTheRealCorpus:
    """A Verification command pinned to `stackowl.jsonl` is blind after any midnight.

    WHY. At UTC rotation the gateway reopens the new file and the CORE KEEPS AN OPEN
    DESCRIPTOR ON THE ROTATED ONE and goes on writing there
    (`project_gateway_core_dual_log_writer_bug`, 2026-07-18, still unfixed). A query
    naming the single file therefore returns 0 for anything the core logged — and a 0
    looks exactly like a check that ran and passed.

    MEASURED 2026-09-07 on D03.2: its live check read **0** on the single file and **21**
    on the glob, every one of those 21 carrying `turns_after < turns_before`. It had read
    OPEN for days while the feature worked perfectly. D05.8, D08.3 and D16.1 each record
    this lesson IN PROSE — and D03.2 had it wrong anyway, which is the whole argument for
    reporting it rather than trusting it to be remembered.

    REPORTED, NEVER GATED, for doc_check's own standing reason: 47 lines across 23
    documents carry it today, and a tripwire would fail every unrelated change until
    someone rewrote twenty-three documents. The deletion-stale count drained 11 -> 2 by
    being visible every loop; this can drain the same way.
    """

    @pytest.mark.tripwire
    def test_the_report_names_the_commands_that_are_actually_there(self) -> None:
        out = subprocess.run(
            [sys.executable, str(_ROOT / "scripts" / "doc_check.py")],
            cwd=_ROOT, capture_output=True, text=True, timeout=300,
        )

        assert "BLIND AFTER MIDNIGHT" in out.stdout, (
            "doc_check no longer reports single-log-file queries, so a check that can "
            f"only ever return 0 reads like one that passed:\n{out.stdout[-1500:]}"
        )
        m = re.search(r"BLIND AFTER MIDNIGHT — (\d+) Verification command", out.stdout)
        assert m, f"the report lost its count:\n{out.stdout[-1200:]}"
        assert int(m.group(1)) >= 20, (
            f"only {m.group(1)} single-log commands found; FORTY-SEVEN across 23 "
            "documents were measured on 2026-09-07. A detector that quietly narrows is "
            "the failure this file exists to prevent — and this one narrowed TWICE while "
            "being written: the first regex required the command word BEFORE the "
            "filename and missed `cat …/stackowl.jsonl | jq`, reporting 14"
        )

    def test_it_does_not_flag_the_glob(self) -> None:
        """The other direction. A detector that also matched `stackowl*.jsonl` would
        report every correct command as broken — crying wolf on the fix itself."""
        sys.path.insert(0, str(_ROOT / "scripts"))
        import doc_check as dc

        assert dc._single_log_queries(
            'grep -h "x" ~/.stackowl/logs/stackowl*.jsonl | wc -l'
        ) == []
        assert dc._single_log_queries(
            'grep -c "x" ~/.stackowl/logs/stackowl.jsonl'
        ), "the single-file form is no longer detected"
        assert dc._single_log_queries(
            "cat ~/.stackowl/logs/stackowl.jsonl | jq -r '.msg'"
        ), "the `cat … | jq` form — the commonest here — is not detected"


class TestADocumentsVerificationReadsAFieldProductionRecords:
    """The level question, for documents — the population the guard above skips.

    The class above asks whether a Verification grep can MATCH. This asks whether
    what it matches can EXIST, which is the same cure one population over, and the
    third time this file has applied it.

    WHY, measured 2026-09-07. D02.6's Verification ends with a `jq` reading
    `.fields.recovery` and the note "Not proven live". Its own text then explains
    why it never could be: "that line is `log.engine.debug`, and production runs at
    INFO — which is why the `cause` field appears zero times in 15 days of logs".
    The document diagnosed the defect, filed it as a parenthetical, and left the
    query. A month later `recovery` appeared **zero times in 580,225 records** across
    ten retained days. Not "not yet" — impossible.

    A FIELD, NOT A MESSAGE, which is why nothing already here caught it. The closing
    check guard asks whether a grepped MESSAGE LITERAL is emitted at INFO. D02.6 does
    not grep a message; it selects a field key off a JSON record. So the same defect
    walked past a guard built for it, wearing a different shape.

    THE CORPUS IS REAL: 20 design documents read `.fields.X` in a fenced Verification
    block, roughly 60 distinct keys between them, against 576 keys in `src/` that no
    INFO-or-above call ever emits. The intersection is now empty, and it is empty
    BECAUSE of this item — `recovery` was its only member.
    """

    @staticmethod
    def _debug_only_field_keys() -> set[str]:
        """Field keys emitted by `log.*.debug` and by nothing at INFO or above."""
        levels: dict[str, set[str]] = {}
        for path in _SRC.rglob("*.py"):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:  # pragma: no cover — a file mid-edit
                continue
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)):
                    continue
                level = node.func.attr
                if level not in _PRODUCTION_LEVELS | {"debug"}:
                    continue
                for kw in node.keywords:
                    if kw.arg != "extra" or not isinstance(kw.value, ast.Dict):
                        continue
                    for key, val in zip(kw.value.keys, kw.value.values):
                        if not (isinstance(key, ast.Constant)
                                and key.value == "_fields"
                                and isinstance(val, ast.Dict)):
                            continue
                        for fk in val.keys:
                            if isinstance(fk, ast.Constant) and isinstance(fk.value, str):
                                levels.setdefault(fk.value, set()).add(level)
        return {k for k, lv in levels.items() if lv == {"debug"}}

    @staticmethod
    def _fields_read_by_documents() -> dict[str, set[str]]:
        docs = _ROOT / "docs" / "reference-mapping" / "designs"
        out: dict[str, set[str]] = {}
        for doc in sorted(docs.glob("*.md")):
            keys: set[str] = set()
            for block in re.findall(r"```[a-z]*\n(.*?)```", doc.read_text("utf-8"), re.S):
                keys |= set(re.findall(r"\.fields\.(\w+)", block))
            if keys:
                out[doc.name] = keys
        return out

    @pytest.mark.tripwire
    def test_no_document_proves_itself_with_a_field_only_DEBUG_emits(self) -> None:
        debug_only = self._debug_only_field_keys()
        offenders = {
            name: sorted(keys & debug_only)
            for name, keys in self._fields_read_by_documents().items()
            if keys & debug_only
        }

        assert not offenders, (
            "these documents rest their `Last verified` stamp on a log field that "
            "production never records, so the query can only ever return nothing — "
            f"the D02.6 failure, restored: {offenders}"
        )

    def test_the_sweep_sees_both_populations(self) -> None:
        """VACUITY CONTROL. The assertion above passes trivially if either side is
        empty — and both sides are computed, so both can silently go blank."""
        debug_only = self._debug_only_field_keys()
        read = self._fields_read_by_documents()

        assert len(debug_only) >= 100, (
            f"only {len(debug_only)} debug-only field keys found; 576 on 2026-09-07"
        )
        assert len(read) >= 12, (
            f"only {len(read)} documents read a log field; 20 on 2026-09-07"
        )
        assert sum(len(v) for v in read.values()) >= 40, "the key census collapsed"

    def test_the_field_the_item_fixed_is_now_recorded_in_production(self) -> None:
        """THE DISCRIMINATION, named. If `recovery` slid back to DEBUG-only the
        assertion above would fire — this states that it is the case the guard was
        built from, so a future reader can tell the guard has teeth without
        re-deriving the history."""
        assert "recovery" not in self._debug_only_field_keys(), (
            "the taxonomy's recovery action is emitted only at DEBUG again, which is "
            "exactly the state D02.6 sat in for a month"
        )


class TestNoCheckBoundsAtAFutureDate:
    """THE GUARD'S OWN BLIND SPOT, found by walking into it while building the guard.

    A future bound does not produce an empty window — it produces an UNBOUNDED one.
    `log_since.sh` filters by FILENAME, and the current file is `stackowl.jsonl` with no
    date in its name, so it is always included. Measured 2026-09-06:
    `log_since.sh 2026-09-07 scheduler` returned 5,660, the identical count an unbounded
    grep returns.

    So a check dated tomorrow reads CLOSEABLE on evidence that PREDATES the fix, which is
    the exact defect the date bound was added to prevent (DEBT-125: D07.2 closing on
    events seven days older than the code they evidenced). The script now refuses such a
    bound; this catches it at the gate instead, where the check is written.
    """

    @pytest.mark.tripwire
    def test_no_closing_check_is_dated_in_the_future(self) -> None:
        today = datetime.now(timezone.utc).date().isoformat()
        future = {
            f"{item_id}: {bound}"
            for item_id, check in _checks()
            for bound in _log_bounds(check)
            if bound > today
        }

        assert not future, (
            "these closing checks bound at a date that has not happened, so the bound is "
            f"unenforceable and the check counts pre-fix evidence: {sorted(future)}"
        )

    def test_the_bound_check_sees_a_real_population(self) -> None:
        """VACUITY CONTROL, and it is the one that matters here: if no bound parsed, the
        assertion above passes over an empty set."""
        bounds = [b for _i, c in _checks() for b in _log_bounds(c)]

        assert len(bounds) >= 4, f"only parsed {len(bounds)} date bounds"
