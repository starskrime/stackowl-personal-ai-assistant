"""D18.5 — the full run is the programme's only cross-pollution detector, and
over half its verdicts disqualified themselves.

`scripts/full_suite.sh` fingerprints `src` and `tests` before and after the run so
a verdict names the tree it is about. That check was added on 2026-09-04 after a
30-minute run returned `3 failed` for a tree that never existed at any instant —
the suite imported a module at minute 0 and an item edited it at minute 25, so
source-reading assertions compared one version's line numbers against another
version's bytes.

**The fix chosen then was to LABEL the moving run, and its own numbers condemn
it.** Measured 2026-09-05 across every suite log this box has kept: **9 of 17
completed runs — 53% — printed `SUITE TREE CHANGED`**. A detector that voids its
own verdict more than half the time is not a detector, and the label only helped
someone who noticed it and spent another 30 minutes by hand. Nine times, nobody
did. That is fixing WHAT happened and leaving WHY alone: the loop that edits while
the suite runs was never going to stop, so the suite is what had to adapt.

A moving run now re-runs itself ONCE against the tree as it then stands, and says
`SUITE TREE STILL` when the verdict is about a single tree. Proven behaviourally
on 2026-09-05 by touching a source file mid-run: the log recorded TREE CHANGED,
then RETRY, then `TREE STILL (attempt 2)`, then DONE rc=0.

AND THE COMPLETION STAMP IS NOW TRAPPED. It used to be the last statement inside a
`set -euo pipefail` region, below a fingerprint call that runs through a pipe — so
any failure there exited the subshell before the stamp was written. One kept log
proves it happened: `full-suite-20260903-235313.log` ends with `1 failed, 11856
passed … in 1767.71s` and NO `SUITE DONE`, so the documented collector reports a
run that finished 29 hours earlier as still going. A log written to answer "did it
finish?" that cannot answer it is the write-with-no-reader shape the script exists
to cure.
"""

from __future__ import annotations

import pathlib
import re

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_SUITE = _ROOT / "scripts" / "full_suite.sh"


def _body() -> list[str]:
    return [
        line for line in _SUITE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


@pytest.mark.tripwire
def test_the_completion_stamp_cannot_be_skipped() -> None:
    """`SUITE DONE` must be written by a trap, not merely placed last.

    Placed last, it is skipped by any earlier failure under `set -e` — which is
    exactly what happened, and it made the collector answer "still running"
    forever.
    """
    body = "\n".join(_body())
    assert re.search(r"trap\s+'[^']*SUITE DONE[^']*'\s+EXIT", body), (
        "SUITE DONE is not written from an EXIT trap. As a plain final statement "
        "it is skipped whenever an earlier command fails under `set -e`, and the "
        "log then cannot answer the one question it exists to answer."
    )


@pytest.mark.tripwire
def test_a_moving_tree_is_re_run_not_merely_labelled() -> None:
    """The verdict must be about ONE tree, and bounded so it cannot spin."""
    body = "\n".join(_body())

    assert "SUITE RETRY" in body, (
        "a run over a moving tree is only labelled, not re-run. 53% of completed "
        "runs printed TREE CHANGED, and a warning nobody acts on is not a fix."
    )
    assert "SUITE TREE STILL" in body, (
        "nothing states positively that the verdict is about one tree — absence of "
        "a warning is not evidence"
    )
    assert re.search(r"attempt.*-ge\s+2|attempt.*>=\s*2", body), (
        "the retry is not bounded; continuous editing would loop it forever"
    )


@pytest.mark.tripwire
def test_the_run_is_pinned_to_a_deterministic_locale() -> None:
    """CI parity, and the one clause of it that was not vacuous here.

    Pinning TZ is what exposed `_next_local_hour_iso` resolving "the next local
    09:00" against the HOST's zone instead of the operator's `system.timezone` —
    invisible on this box, where the two happen to share an offset. It must be
    PINNED rather than defaulted: `${TZ:-UTC}` leaves a developer's own zone in
    place, which is the opposite of parity.
    """
    body = "\n".join(_body())

    assert re.search(r"^export TZ=UTC$", body, re.M), (
        "TZ is not pinned to UTC. A defaulted `${TZ:-UTC}` keeps the run "
        "host-dependent, which is how a timezone defect stayed invisible here."
    )
    assert re.search(r"^export LANG=", body, re.M), "LANG is not pinned"


def test_the_collect_recipe_does_not_hide_the_failures() -> None:
    """`tail -6` lied on exactly the runs that mattered.

    When the tree-changed warnings fire they push the `FAILED` lines out of the
    window, so the operator learns THAT it failed and never WHAT failed — on the
    runs where that matters most.
    """
    text = _SUITE.read_text(encoding="utf-8")
    recipe = text.split("collect with:", 1)[-1]

    assert "FAILED" in recipe, (
        "the collect recipe never surfaces FAILED lines; a verdict without a "
        "subject is what this script exists to prevent"
    )
    assert not re.search(r"^\s+tail -\d+ \"\$log\"\s*(#|$)", recipe, re.M), (
        "the recipe still recommends a bare `tail -N`, which drops the failures "
        "whenever the tree-changed warnings are present"
    )
