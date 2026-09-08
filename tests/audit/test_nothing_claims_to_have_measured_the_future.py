"""You cannot have measured something tomorrow.

MEASURED 2026-09-08, when `tripwires.sh` refused a commit of mine. A closing check
I had just written was dated 2026-09-09 and
`TestNoCheckBoundsAtAFutureDate::test_no_closing_check_is_dated_in_the_future`
caught it. Chasing that one line found **thirteen more**, across eight files and
two commits ALREADY PUSHED: `found:` dates on two debt records, `MEASURED
2026-09-09` in `runner.py`, in the item-loop skill, in `progress_lint.py`, in
`doc_check.py`, in `PROCESS.md`'s stage table, in two test docstrings, and in a
constant NAMED for the date.

Every one of them asserts a measurement taken a day in the future. None was a
typo: I inferred the date from how long the session had been running instead of
reading the clock — the "measure, do not assume" failure applied to the one fact
that is free to check.

THE GUARD EXISTED AND SAW ONE OF FOURTEEN. `closing_check` fields were covered
because a future date there makes `log_since.sh` return 0 forever; the same wrong
date in a `found:` field, a docstring or a comment was invisible, because nothing
looked. That is this repo's recurring shape — a rule enforced on the surface where
it was first noticed and nowhere else — and the cure is the same one every time:
sweep the SET, not the copy you remember.

WHY IT IS SAFE AS A GATE. "Measured" is past tense and a date is a date: a claim
to have measured a future day is wrong with no interpretation needed, and the
population is ZERO. It cannot fire on correct work, only on the change that makes
the claim. A genuinely future date — a scheduled slot, a deadline — is not a
measurement and is not matched.
"""

from __future__ import annotations

import datetime
import re
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]

#: `MEASURED 2026-09-09`, `Measured 2026-09-09`, `MEASURED, 2026-09-09` — the
#: convention this corpus uses to date an observation. The word is what makes the
#: claim checkable: a bare date could be a deadline, a slot or a plan, and only a
#: PAST-TENSE verb turns it into an assertion about something already seen.
_MEASURED = re.compile(r"\bMEASURED\b[^\n]{0,12}?(\d{4}-\d{2}-\d{2})", re.IGNORECASE)
#: `found: 2026-09-09` — when a debt record says the defect was found.
_FOUND = re.compile(r"^\s*found:\s*(\d{4}-\d{2}-\d{2})\s*$", re.MULTILINE)

#: Directories that record what was true at the time and must not be rewritten.
_HISTORY = ("do_not_push_to_git_research_only", ".git", "docs_archive", "_bmad-output")


def _tracked_text_files() -> list[Path]:
    """Every tracked text file git knows about, minus the dated archives."""
    out = subprocess.run(
        ["git", "ls-files"], capture_output=True, text=True, timeout=120, cwd=_ROOT
    ).stdout.split("\n")
    keep: list[Path] = []
    for rel in out:
        if not rel or any(h in rel for h in _HISTORY):
            continue
        # THIS FILE DEFINES THE PATTERN, so it necessarily contains examples of it.
        # A guard that matched its own documentation would be reporting on the
        # existence of its own regex — the second time in one session a mechanism
        # could not tell a CITATION from a CLAIM (see `_reviewed_shas` in
        # `doc_check.py`, where a reason naming an earlier commit became a second
        # dismissal). Both cures are the same: match only where the claim is made.
        if rel.endswith(Path(__file__).name):
            continue
        if not rel.endswith((".py", ".md", ".yml", ".yaml", ".sh", ".sql")):
            continue
        keep.append(_ROOT / rel)
    return keep


def _future_claims(today: datetime.date) -> list[str]:
    offenders: list[str] = []
    for path in _tracked_text_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):  # pragma: no cover
            continue
        patterns = [_MEASURED]
        if path.name == "progress.yml":
            patterns.append(_FOUND)
        for pattern in patterns:
            for m in pattern.finditer(text):
                try:
                    when = datetime.date.fromisoformat(m.group(1))
                except ValueError:  # pragma: no cover — the regex shape guarantees it
                    continue
                if when > today:
                    line = text[: m.start()].count("\n") + 1
                    offenders.append(
                        f"{path.relative_to(_ROOT)}:{line}  {m.group(0).strip()[:70]}"
                    )
    return offenders


@pytest.mark.tripwire
def test_no_record_claims_a_measurement_from_the_future() -> None:
    today = datetime.datetime.now(datetime.UTC).date()
    offenders = _future_claims(today)
    assert not offenders, (
        f"these claim a measurement dated after today ({today}). A date written from "
        f"memory rather than read from the clock turns every number beside it into an "
        f"assertion about a day that has not happened:\n  " + "\n  ".join(offenders)
    )


@pytest.mark.tripwire
def test_the_sweep_actually_reaches_the_corpus() -> None:
    """VACUITY CONTROL. The assertion above passes on an empty walk.

    A `git ls-files` that returned nothing, an extension filter that matched
    nothing, or a regex that stopped matching would all read GREEN while checking
    no files at all — the shape this repo has now paid for in three instruments.
    """
    files = _tracked_text_files()
    assert len(files) >= 500, f"the walk found only {len(files)} tracked text files"
    # And the detector must still recognise the convention it was built on: a
    # generous future date proves the regex and the comparison both still work.
    ancient = datetime.date(2000, 1, 1)
    assert _future_claims(ancient), (
        "against a year-2000 'today' the sweep found NO dated measurements at all, "
        "so the regex no longer matches the convention this corpus writes"
    )
