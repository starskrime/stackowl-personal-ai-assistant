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
