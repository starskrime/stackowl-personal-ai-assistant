"""A log-reading check becomes a RECORD once its evidence rotates away.

WHY THIS EXISTS — the eighth variant of the closing-check family, and the first whose
cause is TIME rather than wording.

A Verification command that reads the logs and records "PASS 2026-08-21: 8 occurrences"
is a CHECK for as long as the logs reach back that far, and a RECORD afterwards. Once the
evidence rotates the command returns 0 forever, and 0 reads as failure to whoever runs it
next. The seven variants before this one were all about what a check ASKS; this one is
about how long the answer survives. THE EVIDENCE HAD A SHORTER LIFETIME THAN THE DOCUMENT.

MEASURED 2026-09-07: four such commands across three documents, the oldest citing
2026-07-27 against logs that begin 2026-08-28. D16.3's was the sharpest — its evidence
was a throwaway contributor plugin installed to prove the path and then REMOVED, so the
line it greps cannot fire again even in principle.

THE HORIZON IS READ FROM THE FILES, NEVER FROM THE CONFIG. `backupCount` is 30 and
`getFilesToDelete()` returns nothing, yet only ten dated files exist — the horizon is
young, not over-pruned, because a deletion incident on 2026-08-30 left two and daily
rotation has added one since. A detector keyed on the intended 30 would have reported
nothing while three documents cited evidence already gone.

WHAT IS GUARDED IS THE INSTRUMENT. Two false positives were designed out and are pinned
below, because both would cry wolf on documents that had done the work:

  * a FRESHER re-run recorded in the same block clears the finding — D08.1 carries
    "RAN 2026-08-17 -> 4 firings" with "RE-RAN 2026-09-07 -> 99" on the next line;
  * a command QUOTED IN PROSE is not a Verification command. The document that FIXES this
    defect has to quote the retired command to explain it, and the first, line-scoped
    version of this detector flagged that correction as the defect — the same shape as the
    escalation report reading its own explanation one loop earlier.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "scripts"))

from doc_check import _evidence_older_than_the_logs, _log_horizon  # noqa: E402

_HORIZON = "2026-08-28"


@pytest.mark.tripwire
def test_it_finds_a_fenced_command_whose_evidence_has_rotated() -> None:
    doc = (
        "## Verification\n\n```bash\n"
        "grep -h 'plugin parts contributed' ~/.stackowl/logs/stackowl*.jsonl | tail\n"
        "# PASS 2026-08-21: 8 occurrences.\n```\n"
    )
    found = _evidence_older_than_the_logs(doc, _HORIZON)
    assert len(found) == 1, found
    assert found[0][1] == "2026-08-21"


@pytest.mark.tripwire
def test_a_fresher_re_run_in_the_same_block_clears_it() -> None:
    """The D08.1 shape. Flagging this would cry wolf on a document already corrected."""
    doc = (
        "```bash\n"
        "grep -h 'nudge: due' ~/.stackowl/logs/stackowl*.jsonl | wc -l\n"
        "# PASS: >0. RAN 2026-08-17 -> 4 firings.\n"
        "# RE-RAN 2026-09-07 -> 99 firings over ten retained days.\n```\n"
    )
    assert _evidence_older_than_the_logs(doc, _HORIZON) == []


@pytest.mark.tripwire
def test_a_command_quoted_in_prose_is_not_a_verification_command() -> None:
    """The correction shape — and the first version of this detector failed it.

    A document retiring a rotted check must QUOTE the command to explain what it retired.
    That is a description, not an instruction, and only a fence distinguishes them.
    """
    doc = (
        "The command this section used to carry was\n"
        "`grep -h 'plugin parts contributed' ~/.stackowl/logs/stackowl*.jsonl`, with **PASS\n"
        "2026-08-21: 8 occurrences**. Re-run today it returns 0, and it always will.\n"
    )
    assert _evidence_older_than_the_logs(doc, _HORIZON) == []


@pytest.mark.tripwire
def test_an_unknowable_horizon_reports_nothing_rather_than_everything() -> None:
    """Degrade to silence, never to a wrong report: with no logs there is no horizon, and
    every recorded date would otherwise look older than it."""
    doc = "```bash\ngrep x ~/.stackowl/logs/stackowl.jsonl\n# PASS 2020-01-01: 3\n```\n"
    assert _evidence_older_than_the_logs(doc, "") == []


@pytest.mark.tripwire
def test_the_horizon_actually_reads_the_files() -> None:
    """THE CONTROL. An empty horizon silences the whole report, so a broken reader and a
    clean corpus print the same thing. Assert the CAPABILITY — a real ISO date back from
    the log directory — never how many documents it happens to catch."""
    horizon = _log_horizon()
    assert horizon, "no horizon: the report would be silently empty"
    assert len(horizon) == 10 and horizon.count("-") == 2, horizon
