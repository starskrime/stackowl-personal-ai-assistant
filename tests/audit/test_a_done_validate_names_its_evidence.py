"""A `validate: done` is the stage nobody looks at again, so it must name its evidence.

THE FOURTH INSTANCE OF ONE CURE, and the case that found it was my own. An
escalation's premise aged silently until `premise_check`; a `partial` stage's
evidence aged silently until `closing_check`; a design document's claim aged
silently until `doc_check`. A `validate: done` had no check of any kind — and
`done` is precisely the word that stops anybody asking again.

MEASURED 2026-09-08. DEBT-230's record was DRAFTED IN THE SCRATCHPAD while a full
suite ran, and applied verbatim when the tree came free. It carried
`validate: done`. The platform was never restarted, no live drop record was ever
read, and at the time of the correction there were 61 such records in the retained
logs — ALL of them the old `{"has_app": true}` shape and ZERO carrying the new
payload. The record asserted an intention as a fact, and nothing could catch it,
because nothing asked.

THE FIRST DETECTOR I WROTE PASSED IT, which is why the rule is a NAME and not a
heuristic. That version scored an entry as evidenced if it held any key matching
/valid|live|observed|measured/ longer than 120 characters. DEBT-230 has
`the_live_cost_is_on_the_adapter_that_DID_carry_a_payload` — a description of the
DEFECT — so it scored as evidenced while being the single worst offender in the
file. **A key whose name resembles evidence is not evidence**, which is this
repo's denominator rule biting the instrument built to enforce it.

TWO HONEST WAYS TO SATISFY IT. A mapped item's evidence belongs in its design
document's Verification section, so a `doc:` key discharges the requirement.
Evidence-led work has no document, so it names the evidence in a `validated…` key
— or records the stage `partial` with a `closing_check`, which is what the
honest-validate rule prescribes for a claim reality has not settled yet. DEBT-230
took the second road: `partial`, with a check that returns CLOSEABLE the day a
drop record carries `text_len`.

WHY A RATCHET AND NOT A GATE. 43 records predate the convention, and a gate that
failed on all 43 would fail every unrelated change until someone re-validated
forty-three items — which is how a gate gets bypassed rather than satisfied, the
same reason `doc_check` is a report. The number can only FALL, so this fires on a
new unevidenced claim and on nothing else.

`current` IS EXCLUDED and the exclusion is stated rather than hidden: it holds 133
such records against 43 here, and it is a JOURNAL — `progress_lint`'s own summary
calls 281 of its 294 keys "journal and prunable". Pinning a number made of two
different kinds of record is the denominator error this programme pays for most.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "scripts"))
from progress_lint import (  # noqa: E402
    entries_with_an_unevidenced_done_validate as _unevidenced,
)

#: The population on the day the rule was written, AFTER DEBT-230 was corrected.
#: A ceiling, never a target: it may fall as records are validated or re-recorded,
#: and any rise is a new claim nobody can check.
_MEASURED_2026_09_08 = 26
#: 43 -> 26 on 2026-09-11 (DEBT-303), and the drop is a MEASUREMENT ERROR CORRECTED,
#: not seventeen records validated. The walk matched the literal prefix `validated`
#: while the corpus writes `validate_result`, `validate_evidence_<date>`, a bare
#: `validate`, `validate_CLOSED_<date>` and `validate_done_because` — so 17 records
#: that named their evidence in full were counted as naming none. It now matches by
#: SHAPE. A ceiling is only worth ratcheting against if its denominator is real.


def _record() -> dict:
    return yaml.safe_load((_ROOT / "progress.yml").read_text(encoding="utf-8"))


@pytest.mark.tripwire
def test_no_new_done_validate_arrives_without_its_evidence() -> None:
    found = _unevidenced(_record())
    assert len(found) <= _MEASURED_2026_09_08, (
        f"{len(found)} records claim `validate: done` while naming neither a design "
        f"document nor a `validated…` key, against {_MEASURED_2026_09_08} measured on "
        f"2026-09-08. A `done` nobody can check is a claim, not a check. Either add a "
        f"`validated…` key naming what you observed LIVE, or record the stage "
        f"`partial` with a `closing_check` — the honest state for a claim reality has "
        f"not settled yet. New: {sorted(set(found))[-5:]}"
    )


@pytest.mark.tripwire
def test_the_detector_still_sees_the_population_it_was_built_on() -> None:
    """VACUITY CONTROL, and it is not hypothetical here.

    The assertion above passes when `found` is EMPTY, so a detector that stopped
    matching — a renamed stage key, a restructured record, a pool that moved —
    would read green while guarding nothing. This repo has now paid for that shape
    in two separate instruments, so every ratchet gets its floor.

    THE FLOOR USED TO BE A LIVE COUNT (`>= 30`) AND THAT WAS THE WRONG SHAPE. This
    programme exists to DRIVE THAT POPULATION DOWN, so a floor under it fails the day
    the work succeeds — and lowering the number only moves the date: 30 becomes 20
    becomes 0. It failed here for exactly that reason, when the walk was corrected on
    2026-09-11 and the count fell 43 -> 26 without a single record changing meaning.
    `CLAUDE.md` records the identical failure in `test_the_sweep_sees_a_real_population`
    on 2026-09-10, and the cure it landed on is the one used here: CONSTRUCT the
    population, so the control is independent of how much real work remains.
    """
    # A record that MUST be flagged: done validate, no doc, no evidence key.
    blind = {"id": "SYNTHETIC-blind", "stages": {"validate": "done"}}
    # And three that must NOT be, one per way of discharging the requirement.
    doc = {"id": "SYNTHETIC-doc", "stages": {"validate": "done"}, "doc": "D00.0.md"}
    evidenced = {
        "id": "SYNTHETIC-evidenced",
        "stages": {"validate": "done"},
        "validate_result": "observed live at 00:00",
    }
    # The spelling that was invisible until 2026-09-11 — pinned so it cannot regress.
    older_spelling = {
        "id": "SYNTHETIC-older-spelling",
        "stages": {"validate": "done"},
        "validate_evidence_2026_09_08": "observed live",
    }
    # A `done` whose ONLY validate-ish key EXPLAINS WHY IT WAS PARTIAL. That is not
    # evidence, it is the record contradicting its own stage, and eight live records
    # carried exactly this until 2026-09-11. They were re-keyed to the past tense in
    # the same change, which EMPTIED the population that pinned the exclusion —
    # mutation testing then showed removing it broke nothing. So the case is
    # constructed here rather than relied upon: this repo has already paid twice for a
    # guard that pins today's DATA instead of the RULE.
    contradicts_itself = {
        "id": "SYNTHETIC-partial-narration",
        "stages": {"validate": "done"},
        "validate_is_PARTIAL_and_the_reason_is_named": "waiting on a scheduled job",
    }
    synthetic = {
        "items": [],
        "known_debt": [blind, doc, evidenced, older_spelling, contradicts_itself],
    }

    got = set(_unevidenced(synthetic))

    assert got == {"SYNTHETIC-blind", "SYNTHETIC-partial-narration"}, (
        f"the walk no longer recognises the shape it was built to find. Expected "
        f"exactly the two unevidenced records; got {sorted(got)}"
    )

    # Separately, and deliberately only NON-EMPTY: the live corpus must still be
    # reachable. This says the walk runs against the real record without pinning a
    # number the programme is trying to reduce.
    assert _unevidenced(_record()), (
        "the live record now reports ZERO unevidenced done-validates. That is the "
        "goal, and it is also what a broken walk looks like — confirm against "
        "`uv run python scripts/progress_lint.py` before deleting this control."
    )


@pytest.mark.tripwire
def test_a_design_document_discharges_the_requirement() -> None:
    """The rule must not push mapped items into duplicating their document here.

    A `doc:` entry's Verification section IS the evidence, and `doc_check.py`
    already keeps it honest. If this stopped being true the rule would be asking
    for the same fact in two places — the two-copies defect, created by the guard
    against a different one.
    """
    data = _record()
    documented = [
        e for e in data.get("items", [])
        if (e.get("stages") or {}).get("validate") == "done" and e.get("doc")
    ]
    assert documented, "no documented item has a done validate — the exemption is dead"
    flagged = set(_unevidenced(data))
    assert not {str(e.get("id")) for e in documented} & flagged, (
        "an item whose evidence lives in its design document was flagged anyway"
    )
