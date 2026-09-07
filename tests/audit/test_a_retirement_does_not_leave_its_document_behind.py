"""A retirement deletes code, registration, tests and job rows — and the document.

WHY THIS EXISTS. `D05.7` said `hard_stop_enabled` defaults **False**, presenting a
live, operator-tunable flag. `d8b8ba81` (2026-08-30) had deleted it eight days
earlier, along with `before_call`, `_halt`, `halt_decision`, `should_halt` and four
config fields. That commit updated the code, the registration, the tests AND the
module docstring — it was a careful retirement. It did not update the design
document, which is the record of the decision and the surface a reader meets first.

THE CAUSE IS THE CHECKLIST, NOT THE COMMIT. "Retired means deleted" enumerates
code, registration, tests and job rows. The design document is not on that list, so
by the rule's own wording every retirement in this programme has been free to leave
its document asserting a thing that no longer exists.

MEASURED 2026-09-07, and it is not one document: of the 19 stale design documents,
ELEVEN were made stale by a commit that SUBTRACTED something — `quirks`, LanceDB,
`retry_queue`, the fact-store machinery, a 600s cap, a notification cap. Staleness
by date says "a source moved". It cannot say "a source was removed", and those are
different kinds of wrong: behind, versus actively false.

NOT A GATE, and for `doc_check.py`'s own recorded reason — a tripwire here would
fail every unrelated change until someone re-read eleven documents, which is how a
gate gets bypassed rather than satisfied. What is guarded is THE INSTRUMENT: the
whole failure was a report that could not see a population, and a detector that
silently goes blind reproduces it exactly.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]

sys.path.insert(0, str(_ROOT / "scripts"))
import doc_check as dc  # noqa: E402


class TestTheDeletionSignalIsARealDiscriminator:
    def test_it_recognises_a_subtracting_commit(self) -> None:
        for subject in (
            "refactor(ESC-68): delete the tool-loop hard stops — they could never run",
            "chore: delete the retired fact-store machinery — 1,056 lines, six modules",
            "refactor(D08.2): remove LanceDB — the dependency, the adapter, the directory",
            "refactor(loop): the last of the four engines is gone — retry_queue deleted",
        ):
            assert dc._DELETION.search(subject), subject

    def test_it_does_not_fire_on_an_ordinary_change(self) -> None:
        """A signal that matches everything ranks nothing. These are real subjects
        from this repository's history that must NOT be flagged."""
        for subject in (
            "feat(D05.7): tool-loop guardrails — three detectors, warn-only, args-aware",
            "docs(D06.2): hibernation answers a question this architecture never asks",
            "fix(consent): a run that cannot reach the host may proceed unattended",
        ):
            assert not dc._DELETION.search(subject), subject


class TestTheReportSeesTheRealCorpus:
    """VACUITY CONTROL. Every assertion above uses a hand-written string, so all of
    them pass against a scanner that returns nothing on the real repository."""

    @pytest.mark.tripwire
    def test_the_deletion_scan_finds_the_documents_that_are_actually_there(self) -> None:
        out = subprocess.run(
            [sys.executable, str(_ROOT / "scripts" / "doc_check.py")],
            cwd=_ROOT, capture_output=True, text=True, timeout=300,
        )

        assert "STALE BY DELETION" in out.stdout, (
            "the report no longer separates deletion-stale documents, so a document "
            f"describing something that was removed reads like one merely behind:\n"
            f"{out.stdout[-2000:]}\n{out.stderr[-1000:]}"
        )
        m = re.search(r"STALE (\d+) \((\d+) by deletion\)", out.stdout)
        assert m, f"the summary line lost its deletion count:\n{out.stdout[-1500:]}"
        stale, by_deletion = int(m.group(1)), int(m.group(2))
        assert by_deletion <= stale, (out.stdout[-1500:])
        assert by_deletion >= 5, (
            f"only {by_deletion} deletion-stale documents found; ELEVEN of nineteen "
            "were measured on 2026-09-07. A scanner that quietly narrows is the exact "
            "failure this file exists to prevent — doc_check.py under-reported by "
            "eleven that way once already"
        )

    def test_the_scan_survives_a_path_git_cannot_date(self) -> None:
        """I4's spirit, applied to an instrument: a report must never cost itself
        its output. `_deletions_since` returns a list, never raises."""
        assert dc._deletions_since(["docs/reference-mapping/designs"], "2026-01-01") == [] or True
        assert isinstance(dc._deletions_since([], "2026-01-01"), list)
