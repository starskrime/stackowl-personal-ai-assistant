"""The gap is the one claim in the record that decided what to build and checked nothing.

MEASURED 2026-09-11. Picked up A05.2 to build it. Its gap said "the customer
cannot change a setting without editing YAML on the host" — and `/config set` had
existed the whole time, validating the key, writing via `save_yaml`, then
RE-READING the file to confirm the write persisted (F-81).

Of the 25-item Agentic OS series, **17 gaps assert an ABSENCE**. Nine were checked
against the tree and **SEVEN were wrong or overstated**: A05.2 above; A05.4, whose eight cron verbs
already shipped; A01.4, which said stop conditions "were never built" while
ESC-170 IN THE SAME FILE recorded "the budget stop is already on and firing — 101
stops"; and A04.1, a P1 item blocking two others, which said no single agent
descriptor exists on the strength of a grep for `AgentCard` — a name nothing in
this tree was ever going to use, while `OwlAgentManifest` is read by 29 modules.
The record contradicted itself and nothing could notice, because only one of each
pair carried a check.

THE FOURTH INSTANCE OF ONE CURE. `premise_check` for an escalation, `closing_check`
for a partial stage, `Last verified` + `doc_check` for a document — and the gap,
which is upstream of all three, had nothing.

A REPORT, NOT A GATE: eight absence-claiming gaps still carry no check, and a
guard that failed all of them would be bypassed rather than satisfied.
"""

from __future__ import annotations

import importlib.util
import pathlib
import subprocess
import sys

import pytest
import yaml

# parents[1], not [2]: this file sits DIRECTLY in `tests/`. The first version said
# [2] and PASSED in the scratchpad, where the extra level happened to hold a
# `progress.yml` and a `scripts/` — then resolved to `/ssd/projects` on the real
# tree and every assertion raised FileNotFoundError. An off-tree dry run cannot
# see a path bug whose wrong answer is correct off-tree.
_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _mod():
    spec = importlib.util.spec_from_file_location(
        "gap_check", _ROOT / "scripts" / "gap_check.py"
    )
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    sys.modules["gap_check"] = m
    spec.loader.exec_module(m)
    return m


def _items() -> list[dict]:
    data = yaml.safe_load((_ROOT / "progress.yml").read_text(encoding="utf-8"))
    return [i for i in data.get("items", []) if isinstance(i, dict)]


@pytest.mark.tripwire
def test_the_absence_detector_sees_a_constructed_claim_and_not_a_description() -> None:
    """VACUITY CONTROL on a CONSTRUCTED pair, never on the live count.

    A floor under the live number fails the day the class is drained, which is the
    point of reporting it. This repo has paid for that shape three times, so the
    control builds its own population.
    """
    m = _mod()
    assert m._asserts_absence("Cron jobs are invisible unless you query the DB"), (
        "the detector cannot see an absence claim — the report would name nothing"
    )
    assert m._asserts_absence("There is no single descriptor"), "a 'there is no' claim"
    assert not m._asserts_absence(
        "The dashboard should present schedules grouped by owl."
    ), (
        "a DESCRIPTION was read as a claim about absence. A gap that asserts nothing "
        "cannot be refuted, and demanding a check for it is how a report becomes noise"
    )


@pytest.mark.tripwire
def test_every_gap_check_actually_runs_and_answers() -> None:
    """A check that errors is worse than none: it reads as a quiet HOLDS.

    Two of the four written for this item were WRONG on their first run — both
    grepped SOURCE TEXT and matched prose. A01.4's found `hard_stop` in a docstring
    RECORDING ITS DELETION; A05.2's found "config" in an import line. Both printed
    REFUTED against gaps that hold. They ask structural questions now — the settings
    model, and the router's registered routes.
    """
    for item in _items():
        check = item.get("gap_check")
        if not check:
            continue
        out = subprocess.run(
            ["bash", "-c", str(check)], cwd=_ROOT, capture_output=True, text=True
        )
        last = (out.stdout.strip().splitlines() or [""])[-1]
        assert last.startswith(("HOLDS", "REFUTED")), (
            f"{item['id']}'s gap_check printed {last[:120]!r} "
            f"(stderr: {out.stderr.strip()[:160]!r}). A check must answer HOLDS or "
            "REFUTED; anything else is read as silence."
        )


@pytest.mark.tripwire
def test_a_gap_check_names_its_evidence_not_just_a_verdict() -> None:
    """A bare verdict cannot be disagreed with.

    A05.2's prints the router's actual routes — `['/api/v1/health']` — so the next
    reader sees what the check saw. That is the difference between this and the
    first draft, which printed REFUTED from a substring match on an import line.
    """
    checks = [i["gap_check"] for i in _items() if i.get("gap_check")]
    assert checks, "no gap_check exists — this guard has gone blind"
    assert any("print('HOLDS" in c or 'print("HOLDS' in c or "+str(" in c for c in checks), (
        "no gap_check renders anything it observed"
    )


@pytest.mark.tripwire
def test_the_report_is_not_a_gate() -> None:
    """8 absence-claiming gaps carry no check. A guard failing all of them gets
    bypassed rather than satisfied — the recorded reason `doc_check` is a report."""
    src = (_ROOT / "scripts" / "gap_check.py").read_text(encoding="utf-8")
    body = src.split("def main", 1)[1]
    assert "sys.exit(1)" not in body and "raise " not in body, (
        "gap_check can now fail the run. It is a report."
    )


@pytest.mark.tripwire
def test_a_built_items_gap_is_history_and_is_not_reported_as_work() -> None:
    """SEVEN of the twenty absence-claiming gaps belong to FINISHED items.

    D01.6, D01.7, D04.4, D05.2, D10.6, D18.3 and A05.1 — whose control plane
    shipped hours before this script was written — all say a capability does not
    exist, and reality has refuted every one of them. THAT IS WHAT SUCCESS LOOKS
    LIKE. A gap decides what gets BUILT, so a built item's gap has nothing left to
    decide, and listing seven permanent entries beside the actionable ones is how a
    report stops being read.

    Built on a CONSTRUCTED pair rather than the live ids, for the reason this repo
    has paid for three times: a control whose denominator is the population the
    programme exists to change fails the day the work lands.
    """
    m = _mod()
    finished = {"stages": {"implement": "done", "document": "no_change_needed"}}
    open_item = {"stages": {"implement": "done", "validate": "partial"}}

    assert m._is_complete(finished)
    assert not m._is_complete(open_item)
    assert not m._is_complete({}), "an item with no stages is not finished"
    assert not m._is_complete({"stages": {}}), "an empty stage map is not finished"


@pytest.mark.tripwire
def test_the_summary_prints_what_it_filtered_out() -> None:
    """A filtered denominator nobody can see is the error this programme pays for
    most. The complete-item gaps are skipped as WORK and named as HISTORY."""
    src = (_ROOT / "scripts" / "gap_check.py").read_text(encoding="utf-8")
    assert "historical" in src.split("def main", 1)[1], (
        "main() no longer tracks the gaps it filtered out — seven absence-claiming "
        "gaps sit on finished items and would leave the count with nothing said"
    )
