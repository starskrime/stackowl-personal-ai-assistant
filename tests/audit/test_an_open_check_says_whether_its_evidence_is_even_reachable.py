"""OPEN reads as "not yet". Seven checks said OPEN and not one of them meant that.

MEASURED 2026-09-09, draining `doc_check`'s last report with anything in it — OPEN BUT
NOT TRACKED, seven acceptance checks in three documents whose items say `validate: done`.
Two loops earlier I recorded that these were "blocked on evidence their own item must
earn". THREE were already CLOSEABLE on evidence sitting in the logs; the other four are
unreachable while a named configuration holds. Neither is "not yet".

WHY, AND IT IS ONE CAUSE WEARING THREE FACES. Each verdict was drawn from ONE BOUNDED
SAMPLE and then written down as a property of the DESIGN.

* D05.4 concluded *"`I2` is not waiting on traffic. It is waiting on a shape this design
  does not produce"* from 233 rebuilds. Over 1,562 records the shape occurs 11 times, 7 of
  them satisfying the full stated pass — about 1 in 220, so a 233-record window was
  expected to hold roughly one. Its own sentence, *"The reason is structural, not
  statistical"*, is the inversion exactly.
* D10.6 recorded *"step 4 = 0 exactly as the document predicts"* on 2026-09-02. The line
  entered `src/` on 2026-09-01 and first fired at 06:04 on the 2nd — hours AFTER the
  verification commit. 411 firings now.
* D05.8 says I1/I2 *"close only if the cap drops below 79"*. 79 is the CATALOGUE; the path
  that section is about presents a planner subset of 4-11.

**A SAMPLE BOUND IS NOT A DESIGN PROPERTY**, and a zero from a configuration that forbids
the event is not the same zero as a zero from a path that has not run yet. This file pins
the part of that which a script can actually check.

WHAT IS NOT GUARDED, AND WHY — recorded so it is not re-proposed cheaply. The general
rule ("do not conclude impossibility from one sample") is not mechanically checkable: a
script cannot tell a bounded measurement that was honestly reported from one whose
conclusion overreached, and this corpus already pays for guards that cry wolf on correct
work. What IS checkable is that a check which knows its evidence is configuration-blocked
SAYS SO, and that it derives the configuration instead of restating a number.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "scripts"))
from progress_lint import entries_with_closing_checks as _record_checks  # noqa: E402

_DESIGNS = _ROOT / "docs" / "reference-mapping" / "designs"


def _check_for(ident: str) -> str:
    data = yaml.safe_load((_ROOT / "progress.yml").read_text(encoding="utf-8"))
    for name, raw in _record_checks(data):
        if name == ident:
            return raw
    raise AssertionError(f"{ident} carries no closing_check")


class TestAZeroSaysWhichKindOfZeroItIs:
    @pytest.mark.tripwire
    def test_the_configuration_blocked_check_names_its_antecedent(self) -> None:
        """D05.8's clip lines cannot fire while the cap sits above the presented set. A
        bare count there is a zero indistinguishable from *not yet*, and it read that way
        for eighteen days. The OPEN branch must state the blocking condition."""
        check = _check_for("D05.8")
        command = "\n".join(
            ln for ln in check.splitlines() if not ln.lstrip().startswith("#")
        )
        assert "UNREACHABLE BY CONFIGURATION" in command, (
            "the OPEN branch no longer distinguishes a forbidden zero from a not-yet zero"
        )
        assert "cap=" in command and "catalogue=" in command, (
            "the verdict does not carry the two numbers that make the zero legible"
        )

    @pytest.mark.tripwire
    def test_it_DERIVES_the_catalogue_rather_than_restating_it(self) -> None:
        """79 is a measurement, not a constant. It was 40 and then 77 inside three weeks,
        and D05.8's prose still carried a stale one — which is how "below 79" came to be
        applied to a path that presents 4-11. The check reads the live value."""
        command = "\n".join(
            ln for ln in _check_for("D05.8").splitlines()
            if not ln.lstrip().startswith("#")
        )
        assert "presented tools — rebuilt" in command and "fields.tools" in command, (
            "the catalogue size is no longer read off the live rebuild line"
        )
        assert "catalogue=79" not in command, "the catalogue is hardcoded again"

    @pytest.mark.tripwire
    def test_the_check_actually_runs_and_prints_one_verdict(self) -> None:
        """Wired, not merely written. `validate_check.py` executes this every loop, and a
        check that errors prints its stderr as the verdict — which has happened here."""
        import subprocess

        out = subprocess.run(
            ["bash", "-c", _check_for("D05.8")],
            capture_output=True, text=True, timeout=300, cwd=_ROOT,
        )
        verdicts = [
            ln for ln in out.stdout.splitlines()
            if ln.startswith(("OPEN", "CLOSEABLE"))
        ]
        assert len(verdicts) == 1, (out.stdout[-500:], out.stderr[-500:])


class TestTheTwoClosuresCiteTheirEvidence:
    """A closure written without its numbers is the same defect in the other direction:
    the next reader cannot tell a measured close from a tidied-away one."""

    @pytest.mark.tripwire
    def test_D05_4_names_the_record_count_that_refuted_it(self) -> None:
        text = (_DESIGNS / "D05.4.md").read_text(encoding="utf-8")
        assert "1,562" in text, "the widened denominator is gone"
        assert "1 in 220" in text, (
            "the rate that makes the 233-record sample inconclusive is gone — without it "
            "the correction reads as an opinion about the old measurement"
        )

    @pytest.mark.tripwire
    def test_D10_6_keeps_why_its_zero_was_honest_when_taken(self) -> None:
        """The 2026-09-02 reading was a NEAR MISS, not negligence — the line first fired
        hours after the verification commit. Deleting that turns a lesson about timing
        into an accusation about care."""
        text = (_DESIGNS / "D10.6.md").read_text(encoding="utf-8")
        assert "411" in text and "2026-09-02T06:04:13Z" in text
        assert "dc19b973" in text, "the provenance that makes it a near miss is gone"

    def test_the_documents_are_where_this_test_thinks_they_are(self) -> None:
        """VACUITY CONTROL. Every assertion above is a substring search over a file read
        by name, and a renamed document would make all of them pass over nothing."""
        for name in ("D05.4.md", "D05.8.md", "D10.6.md"):
            assert (_DESIGNS / name).is_file(), name
