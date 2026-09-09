"""A recorded-but-unverified claim had no settlement state, so it was measured again.

MEASURED 2026-09-09, on my own three loops. `current` holds a record whose
`THE_REST_RECORDED_NOT_VERIFIED` key lists four product defects a measurement lens
found but did not verify. Across three separate invocations I picked one each time,
spent real measurement on it, and found:

  * "daily jobs missed during downtime are silently dropped" — FIXED by `bdcb288a`,
    and live-evidenced: three replays carry `{recurring: true, replay_missed: false,
    loses_an_occurrence: true}`, the last of them `morning_brief` on 2026-09-09
    13:00:57. Before that commit those three were silently dropped. The claim's
    specifics were also wrong: `replay_missed` is set on 3 of the 15 daily jobs, not 0.
  * "owl_lifecycle-jobmarket builds tasks with no delivery address (185/185)" —
    REFUTED. `stream-miss` is THREE messages; 47 say "delivered via proactive fallback"
    with `status: delivered`.
  * "one skill that can never load (229/229 boots)" — it is `trending-research-owl`,
    and it is ESC-127's OPEN PLACEMENT QUESTION, which is the operator's to answer.
  * "`db_reclaim` permanently stalled" — ESC-160, also the operator's.

So ZERO of the four are live unverified defects: two are settled and two are queued
for him. The record still read as four open items, because nothing could mark a claim
settled — and each loop that read it paid to re-discover that.

THIS IS THE SAME CURE A FOURTH TIME, and naming the other three is the point. An
escalation's premise aged silently until `premise_check`. A `partial` stage's evidence
aged until `closing_check`. A design document's claim aged until `doc_check` made
`Last verified` checkable. A RECORDED PRODUCT CLAIM got nothing: it is prose in
`current`, re-read every loop, never settled.

AND IT IS A POPULATION, NOT ONE RECORD — measured before building anything: SEVEN
records in `current` carry an explicitly unverified claim list. This file reports that
population so it can only fall, exactly as `progress_lint` reports the 43 legacy
validates. It is NOT a gate: failing every unrelated change until seven historical
records are retrofitted is how a gate gets bypassed rather than satisfied.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "scripts" / "escalation_check.py"

#: The key shapes this corpus uses to say "recorded, not verified".
_UNVERIFIED = re.compile(r"NOT_VERIFIED|UNVERIFIED|RECORDED_NOT", re.I)


def _records_with_unverified_claims() -> dict[str, dict]:
    data = yaml.safe_load((_ROOT / "progress.yml").read_text(encoding="utf-8"))
    out: dict[str, dict] = {}
    for key, body in (data.get("current") or {}).items():
        if isinstance(body, dict) and any(_UNVERIFIED.search(k) for k in body):
            out[key] = body
    return out


class TestTheSweepExists:
    @pytest.mark.tripwire
    def test_the_checker_reports_unverified_claims(self) -> None:
        """The cure itself, in the ONE script that already sweeps aging premises.
        A second engine for the same question is what this repo forbids first."""
        source = _SCRIPT.read_text(encoding="utf-8")

        assert "claim_check" in source, (
            "`escalation_check.py` no longer sweeps recorded claims — the fourth "
            "population is unwatched again"
        )
        assert "RECORDED BUT NEVER SETTLED" in source

    @pytest.mark.tripwire
    def test_it_runs_and_names_the_population(self) -> None:
        """DRIVEN, not read. A source-reading assertion cannot tell a live sweep from
        a dead one — this repo has already paid for that twice."""
        out = subprocess.run(
            [sys.executable, str(_SCRIPT)],
            capture_output=True, text=True, timeout=900, cwd=_ROOT,
        )
        assert out.returncode == 0, out.stderr[-500:]
        assert "RECORDED BUT NEVER SETTLED" in out.stdout, out.stdout[-600:]

    def test_the_population_is_real_and_not_one_record(self) -> None:
        """VACUITY CONTROL, and the measurement that justified building anything at
        all: a cure for a population of one is over-building."""
        records = _records_with_unverified_claims()

        assert len(records) >= 5, f"only {len(records)} such records: {list(records)}"


class TestTheWorkedExampleIsSettled:
    @pytest.mark.tripwire
    def test_the_record_that_cost_three_loops_now_carries_its_verdicts(self) -> None:
        """The four claims I re-measured, settled in place so no later loop pays again.
        Pinned by their DISTINGUISHING evidence rather than by the word 'settled', so a
        record that merely relabels them still fails."""
        records = _records_with_unverified_claims()
        lens = next(
            (b for k, b in records.items() if "seven_product_defects" in k), None
        )
        assert lens is not None, list(records)
        text = yaml.safe_dump(lens)

        assert "loses_an_occurrence" in text, "the daily-jobs verdict cites no evidence"
        assert "ESC-127" in text and "ESC-160" in text, "the two operator questions are unnamed"
        assert "stream-miss" in text, "the refuted claim is not named as refuted"

    @pytest.mark.tripwire
    def test_the_verdict_reports_a_count_it_actually_MEASURED(self) -> None:
        """A check that stops measuring still prints a verdict.

        MUTATION-FOUND: replacing the numerator with a literal `r=1` left the check
        printing "SETTLED 1 derived replays" — same shape, same word, a number from
        nowhere. Pinning the number here would be brittle (it rises whenever the
        platform replays another job), so this measures the SAME quantity independently
        and compares. Both move together; a hardcoded one does not move at all.
        """
        records = _records_with_unverified_claims()
        lens = next(b for k, b in records.items() if "seven_product_defects" in k)
        out = subprocess.run(
            ["bash", "-c", lens["claim_check"]],
            capture_output=True, text=True, timeout=600, cwd=_ROOT,
        )
        verdict = next(
            (ln for ln in out.stdout.splitlines() if ln.startswith(("SETTLED", "open"))),
            "",
        )
        assert verdict, out.stdout[-400:]

        claimed = int(re.search(r"(\d+) derived replays", verdict).group(1))
        logs = subprocess.run(
            ["bash", "-c",
             "grep -ah 'recover: replaying missed job' ~/.stackowl/logs/stackowl*.jsonl "
             "| jq -s '[.[]|select(.fields.loses_an_occurrence == true "
             "and .fields.recurring == true and .fields.replay_missed == false)]|length'"],
            capture_output=True, text=True, timeout=600, cwd=_ROOT,
        )
        measured = int((logs.stdout or "0").strip() or 0)

        assert claimed == measured, (
            f"the check reports {claimed} derived replays; the logs hold {measured} — "
            "the verdict is not measuring what it names"
        )

    def test_it_carries_a_runnable_claim_check(self) -> None:
        """A verdict written once ages exactly as the claim did. The check is what
        makes it re-askable — the whole point of the other three cures."""
        records = _records_with_unverified_claims()
        lens = next(b for k, b in records.items() if "seven_product_defects" in k)

        assert (lens.get("claim_check") or "").strip(), "settled by prose, not by a check"
