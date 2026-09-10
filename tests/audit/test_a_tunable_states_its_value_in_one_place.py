"""A tunable's value must be stated in one place, and the second place can be
inside the same file.

`CLAUDE.md` is organised around *"Correcting one copy of a rule is not correcting
the rule"*, and every cure it records widened the set of FILES to sweep: the
suite-hangs claim survived in `SKILL.md`, then in `D04.1.md` and
`SESSION_PROMPT.md`, and `d8b8ba81` left a design document advertising a deleted
flag for eight days. **No sweep of files can see two copies inside ONE file.**

MEASURED 2026-09-09. `db_reclaim.py` stated its run-history retention window in
THREE places — the constant, the docstring of `_prune_run_history` (the method
that performs the deletion), and the module docstring of
`tests/scheduler/handlers/test_the_run_history_is_finally_bounded.py`. Commit
`c628d1bf` tightened the window on the operator's authority and rewrote the
first. The other two went on describing the deliberately loose window that
predated his authorisation, and on saying that tightening it was a decision
escalated to him rather than taken.

By then eleven passes had deleted his rows — 3,909, 4,087 and 4,151 on the last
three, each logging `retention_days: 7`. Someone auditing what this platform
deletes would have read the deleting method and concluded it deletes nothing.

THE CURE WAS TO REMOVE THE OTHER TWO STATEMENTS, NOT TO SYNC THEM, and this test
keeps a fourth from appearing. Note that this docstring states no figure of its
own: quoting the superseded number here would re-arm the very trap it records,
which the first draft did and the tool caught.

THE SIBLING GUARD, and the gap between them is the point.
`test_a_test_asks_the_constant_instead_of_restating_it.py` already flags a test
that restates a constant's values IN CODE, and it is deliberately narrow: exact
set equality on a literal collection. It could not have seen this, because the
duplicate here is PROSE, the value is SUPERSEDED rather than current, and the
tunable is a scalar. The code copy of a rule was guarded; the copy a reader
actually reads was not.

It costs ~32s (MEASURED 2026-09-09) because the git step is what makes it
precise: without it the same scan reports 57 constants and is noise. That is
~12% on top of a gate already at ~4.5 minutes, and it buys the only check that
sees a rationale duplicated ACROSS files.
"""

from __future__ import annotations

import functools
import importlib.util
import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]


@functools.lru_cache(maxsize=1)
def _load_report():
    spec = importlib.util.spec_from_file_location(
        "superseded_constants", _ROOT / "scripts" / "superseded_constants.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["superseded_constants"] = mod
    spec.loader.exec_module(mod)
    return mod


@functools.lru_cache(maxsize=1)
def _scan():
    """One scan for the whole module.

    The git step costs ~15s (MEASURED 2026-09-09) and four tests want the same
    answer. Paying it four times would make this the most expensive guard in a
    gate that already doubled in two days, and an expensive gate gets skipped —
    which is the failure mode, not the cost.
    """
    return _load_report().scan_tree()


#: Prose that names a superseded value for a GOOD REASON, with the count of
#: such sites. Two different good reasons occur here, and they are not the same
#: thing:
#:
#:   * HISTORY KEPT ON PURPOSE — a rationale block that says "IT SHIPPED AT 100
#:     FIRST, deliberately" is recording how the value was reached.
#:   * A COLLISION — a MEASUREMENT whose figure happens to equal a value the
#:     constant once held. The git filter removes almost every measurement from
#:     this report, and cannot remove one that collides.
#:
#: Only a reader can tell either from an edit someone missed, so each is judged
#: once and recorded here.
#:
#: THE COUNT IS PART OF THE JUDGEMENT, and that is deliberate. Keying an
#: exemption by LINE would expire it the moment anything above it is edited —
#: the silently-expiring exemption this repo has already paid for. Keying it by
#: file+constant+value alone would let a SECOND, stale mention of the same number
#: hide behind the first, which is exactly the defect that prompted this test.
#: The count closes that gap without pinning a line.
_ACCEPTED: dict[str, tuple[int, str]] = {
    "src/stackowl/memory/curated.py::NUDGE_INTERVAL_TURNS::10": (
        1,
        "A COLLISION, not a claim. The flagged line is a row of the measured "
        "traffic table — '2026-08-11  5 boots  10 turns  max 4 per lifetime' — "
        "where 10 is the turns OBSERVED that day and happens to equal the value "
        "the constant then held. The block's actual statement of the old value, "
        "'WAS 10 — the reference platform's default', is not even the line "
        "reported. Judged by reading the line the tool named rather than the "
        "one the file made obvious.",
    ),
    "src/stackowl/scheduler/handlers/db_reclaim.py::"
    "_RUN_HISTORY_RETENTION_DAYS::100": (
        1,
        "'IT SHIPPED AT 100 FIRST, deliberately' — the constant's own block "
        "records that the loop capped the table without deleting his data until "
        "he authorised it. That sequence is the justification for the 7.",
    ),
    "tests/scheduler/handlers/test_the_run_history_is_finally_bounded.py::"
    "_RUN_HISTORY_RETENTION_DAYS::100": (
        1,
        "HISTORY KEPT ON PURPOSE, in the one place it belongs: "
        "`test_the_window_is_the_one_the_operator_chose` PINS the constant and "
        "explains the sequence that produced the value it pins. The STALE copy "
        "in the same file — a module docstring restating the window and the "
        "safety argument — is what this item removed.",
    ),
}


@pytest.mark.tripwire
def test_no_file_states_a_value_its_own_constant_no_longer_has() -> None:
    """The ratchet. A NEW site fails BY NAME, so the reader is not left counting."""
    report = _scan()
    seen: dict[str, list[int]] = {}
    for site in report.sites:
        seen.setdefault(site.key, []).append(site.lineno)

    unknown = {k: v for k, v in seen.items() if k not in _ACCEPTED}
    assert not unknown, (
        "prose states a value its own constant no longer has, and nobody has "
        "judged it:\n"
        + "\n".join(f"  {k}  at line(s) {v}" for k, v in sorted(unknown.items()))
        + "\n\nRun `uv run python scripts/superseded_constants.py`. Either the "
        "prose is stale — fix it, preferring to DELETE the second statement "
        "rather than sync it — or the history is kept on purpose, in which case "
        "add the key to _ACCEPTED with the reason."
    )

    grown = {
        k: (len(v), _ACCEPTED[k][0]) for k, v in seen.items()
        if k in _ACCEPTED and len(v) > _ACCEPTED[k][0]
    }
    assert not grown, (
        "an accepted key gained further sites — a second mention of the same "
        "superseded value, which is the shape this test exists to catch:\n"
        + "\n".join(f"  {k}: {now} sites, {was} judged" for k, (now, was) in grown.items())
    )


@pytest.mark.tripwire
def test_no_exemption_outlives_the_prose_it_excuses() -> None:
    """A stale exemption is the same defect wearing the reviewer's badge."""
    report = _scan()
    live = {site.key for site in report.sites}
    dead = sorted(set(_ACCEPTED) - live)
    assert not dead, (
        "these keys are excused and no longer occur — the prose was rewritten "
        "and the exemption was not:\n" + "\n".join(f"  {k}" for k in dead)
    )


@pytest.mark.tripwire
def test_the_git_step_is_actually_reading_history() -> None:
    """THE CONTROL, and it is not hypothetical — this exact failure happened.

    While the tool was being written it printed `0 site(s)` with every
    denominator intact — 575 constants, 177 carrying a unit, 38 whose prose names
    another figure — because it was being run from a staging directory that was
    not a git checkout. `git log` returned nothing, so every candidate was
    filtered away. **A clean tree and a broken history lookup print the same
    result**, which is the instrument-lies shape `CLAUDE.md` opens on. Without
    this control the whole guard could pass for ever while measuring nothing.
    """
    mod = _load_report()
    past = mod.past_values(
        "src/stackowl/scheduler/handlers/db_reclaim.py",
        "_RUN_HISTORY_RETENTION_DAYS",
    )
    assert 100.0 in past, "git history no longer yields the 100-day value"
    assert 7.0 in past, "git history no longer yields the current 7-day value"


@pytest.mark.tripwire
def test_the_scan_still_has_candidates_to_filter() -> None:
    """The other half of the same control: the UNIT stage must not silently empty.

    If `UNITS` stopped matching — a rename, a suffix convention change — the
    candidate set would fall to zero and the guard would pass by having nothing
    to look at. 38 constants had prose naming another figure when this was
    written; the floor is deliberately well below that so ordinary drift does
    not trip it.
    """
    report = _scan()
    assert report.constants > 400, report.constants
    assert report.with_unit > 100, report.with_unit
    assert report.with_prose >= 20, (
        f"only {report.with_prose} constants have prose naming another figure "
        "in their unit — the scan has stopped finding candidates, so a clean "
        "result proves nothing"
    )
    assert not report.unreadable, report.unreadable


def test_a_measurement_in_prose_is_not_a_finding() -> None:
    """The discriminator, stated as a test rather than as a claim.

    Prose here is full of numbers that are EVIDENCE — "19,285 pages sat free",
    "384,429,704 tokens", "the oldest row was 92 days old". None is a statement
    of a setting, and a detector that flagged them would be switched off within
    a week. What separates them is that the constant never held those values.

    IT IS A FILTER, NOT A PROOF, and the residue is in `_ACCEPTED` above: the
    `curated.py` entry is a measurement whose figure COLLIDES with a superseded
    value, so it survives the filter while being no kind of claim. The git step
    takes this corpus from 38 candidates to 2; one of the 2 is a collision.
    Stating the tool as exact would be the more comfortable sentence and the
    false one.
    """
    mod = _load_report()
    spans = [(1, "the freelist held 19285 pages while we asked for 2000")]
    stated = mod.stated_values(spans, "pages", exclude=2000.0)
    assert 19285.0 in stated

    past = mod.past_values(
        "src/stackowl/scheduler/handlers/db_reclaim.py", "_DEFAULT_MAX_PAGES"
    )
    assert 19285.0 not in past, (
        "19,285 was never this constant's value — if it were, the filter would "
        "not be separating measurements from settings"
    )
