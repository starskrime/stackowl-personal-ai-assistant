"""A closing query must be written where something can run it — or at least be NAMED.

WHY THIS EXISTS, and it is the fourth time this programme has cured one disease.

An escalation's premise aged silently until `premise_check`. A `partial` stage's
evidence aged silently until `closing_check`. A design document's `Last verified` aged
silently until `doc_check.py`. Each cure was keyed to a STATUS — the entry is an
escalation, the stage is partial, the file is a design doc — when the property being
protected is *this claim is not yet evidenced*, and that property does not care what
status its record carries.

MEASURED 2026-09-06. DEBT-105 is recorded `no_change_needed`, and its own text says:

    "55 heavy rounds after the fix, 19 of them telegram. That is a small sample and the
     direction is what is claimed, not the magnitude. Closing query: re-run the same
     before/after split over a full day of traffic on 2026-09-02."

Four days passed and nothing ran it — not because it was hard, but because nothing
ENUMERATED it. `validate_check.py` walks items whose stage is `partial`, plus debts that
ALREADY carry a `closing_check`. A settled record holding a prose promise is invisible to
the one tool built to stop promises being written in prose.

Run on 2026-09-06 it did not overturn the decision — telegram heavy-round output fell
1,055 -> 642 while the non-telegram control moved 1,486 -> 1,410, so the channel-shape fix
works and no cap is needed. It did correct the record: the "77% reduction" compared
telegram AFTER (476) against the ALL-CHANNEL BEFORE (2,108), two different populations.
Like for like it is 39%. A denominator switch, sitting unread in the state of record.

THIS FILE DOES NOT GATE ON THE BACKLOG, deliberately, and the precedent is `doc_check.py`:
"a tripwire would fail every unrelated change until someone re-read fifteen documents,
which is how a gate gets bypassed rather than satisfied". 38 promises exist and several
genuinely cannot be written yet, because they wait on traffic that has not happened.

So what IS guarded is the instrument, and that is the right target. The whole failure was
an enumeration that could not see a population; a detector that silently goes blind
reproduces it exactly. `doc_check.py` has already done this once — its first version
under-reported by ELEVEN because its parser missed `Source (new):` and src-relative paths,
so documents that HAD followed the convention were filed as having no header at all.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[2]

sys.path.insert(0, str(_ROOT / "scripts"))
from progress_lint import prose_closing_promises  # noqa: E402


def _record() -> dict:
    return yaml.safe_load((_ROOT / "progress.yml").read_text(encoding="utf-8"))


class TestTheDetectorCanTellAPromiseFromNarration:
    """The colon is the whole discriminator, and it is doing real work: matching the
    bare phrase `closing query` reports 117 records, nearly all of them narration about
    a check that already ran. These two cases are that distinction, pinned."""

    def test_it_finds_a_promise(self) -> None:
        found = prose_closing_promises(
            {"current": {"rec": {"note": "Closing query: grep the INFO line tomorrow."}}}
        )

        assert [(r, t) for r, _f, t in found] == [
            ("rec", "grep the INFO line tomorrow.")
        ], found

    def test_it_ignores_narration_about_a_check_that_ran(self) -> None:
        found = prose_closing_promises(
            {"current": {"rec": {"note": "the closing query returned 213 rows"}}}
        )

        assert found == [], (
            "narration was reported as an unkept promise; the report becomes noise and "
            "a reader learns to skim it, which is how the original one went unread"
        )

    def test_a_record_with_a_runnable_check_is_not_reported(self) -> None:
        """Prose beside an executable check is commentary, not an orphan."""
        found = prose_closing_promises(
            {
                "known_debt": [
                    {
                        "id": "DEBT-1",
                        "closing_check": "echo OPEN",
                        "note": "Closing query: grep the line.",
                    }
                ]
            }
        )

        assert found == [], found

    def test_the_text_under_a_closing_check_key_is_never_itself_a_promise(self) -> None:
        found = prose_closing_promises(
            {"items": [{"id": "D1.1", "closing_check": "echo 'Closing query: x'"}]}
        )

        assert found == [], "the executable check was reported as needing one"


class TestTheLabelIsSomethingAHumanCanSearchFor:
    def test_a_known_debt_entry_is_named_by_its_id_not_its_position(self) -> None:
        """`known_debt` is a LIST. Labelling by index gives a reader "14", which names
        nothing and changes whenever an entry is inserted above it."""
        found = prose_closing_promises(
            {"known_debt": [{"id": "DEBT-99", "note": "Closing query: run it."}]}
        )

        assert [r for r, _f, _t in found] == ["DEBT-99"], found


class TestTheDetectorSeesTheRealCorpus:
    """VACUITY CONTROL. Every assertion above uses a hand-built fixture, so all four
    would pass against a detector that returns nothing on the real file."""

    @pytest.mark.tripwire
    def test_it_finds_the_promises_that_are_actually_there(self) -> None:
        found = prose_closing_promises(_record())

        assert len(found) >= 20, (
            f"only {len(found)} promises found in progress.yml; 38 were measured on "
            "2026-09-06. A detector that quietly narrows is the exact failure this "
            "file exists to prevent — doc_check.py under-reported by eleven that way"
        )

    @pytest.mark.tripwire
    def test_it_still_finds_the_archetype(self) -> None:
        """DEBT-105's promise is the case that motivated all of this. If the parser
        ever stops seeing it, the parser is wrong, not the record."""
        texts = [t for _r, _f, t in prose_closing_promises(_record())]

        assert any("before/after split" in t for t in texts), (
            "the archetype promise is no longer detected"
        )

    def test_every_promise_carries_the_text_of_what_to_run(self) -> None:
        """A report naming a record but not its query sends the reader back to the
        file, which is the friction that kept these unread."""
        empty = [
            (r, f) for r, f, t in prose_closing_promises(_record()) if len(t.strip()) < 10
        ]

        assert not empty, f"promises reported with no query text: {empty}"


class TestTheReportIsActuallyWired:
    """BUILT-BUT-NOT-WIRED is defect shape 5 in this codebase, and it would be a
    particularly stupid way to fail here: the entire defect being fixed is a check that
    nothing enumerated. A detector nothing calls is that same bug with a new name."""

    @pytest.mark.tripwire
    def test_validate_check_calls_the_detector(self) -> None:
        tree = ast.parse((_ROOT / "scripts" / "validate_check.py").read_text("utf-8"))
        called = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }

        assert "prose_closing_promises" in called, (
            "validate_check.py no longer calls the detector, so the promises are "
            "invisible again — which is the whole defect"
        )

    @pytest.mark.tripwire
    def test_the_loop_actually_prints_them(self, tmp_path: Path) -> None:
        """The end-to-end control: the assertion above proves a call EXISTS in the
        source, not that running the script reaches it. The unwiring mutant proved
        both are needed — deleting the call site left the AST test green.

        RUN AGAINST A FIXTURE TREE, NOT THIS REPO, and that is not fastidiousness.
        `validate_check.py` executes every `closing_check` in the record as a shell
        command with a 180s timeout each. Pointing it at the real `progress.yml` from
        inside the gate — which runs on every commit — buys a dependency on the live
        database and log files, and a worst case of sixteen checks x 180s. The script
        derives its root from its own location, so a fixture tree relocates it
        entirely: the wiring is still proven, by the cheapest thing that can prove it.
        """
        (tmp_path / "scripts").mkdir()
        for name in ("validate_check.py", "progress_lint.py"):
            (tmp_path / "scripts" / name).write_text(
                (_ROOT / "scripts" / name).read_text(encoding="utf-8"), encoding="utf-8"
            )
        (tmp_path / "progress.yml").write_text(
            yaml.safe_dump(
                {
                    "items": [],
                    "known_debt": [],
                    "current": {"rec": {"note": "Closing query: grep the INFO line."}},
                }
            ),
            encoding="utf-8",
        )

        out = subprocess.run(
            [sys.executable, str(tmp_path / "scripts" / "validate_check.py")],
            cwd=tmp_path, capture_output=True, text=True, timeout=120,
        )

        assert "PROSE PROMISES" in out.stdout, (
            "the report did not appear in the loop's output:\n"
            f"{out.stdout[-2000:]}\n{out.stderr[-1000:]}"
        )
        assert "grep the INFO line." in out.stdout, (
            "the report named a record but not the query to run, which is the friction "
            f"that kept these unread:\n{out.stdout}"
        )


class TestAClosedDebtStopsBeingListedAsOutstanding:
    """A report that cannot tell a CLOSED claim from an OPEN one is noise.

    `validate_check.py` folded `known_debt` into the report by writing
    `stages={"validate": "partial"}` UNCONDITIONALLY. The fold was right — evidence-led
    work lives in `known_debt` and its claims deserve the same re-running — but the
    stage was fabricated rather than read, so a debt recorded `validate: done` stayed
    listed forever, and once its evidence arrived it became a permanent CLOSEABLE that
    no action could clear.

    MEASURED 2026-09-07 while closing DEBT-153: its stage became `done` and the report
    still called it a partial validate needing attention. That is the same disease the
    prose-promise report exists to prevent, one population over — a reader who sees an
    item that never clears learns to skim the list.
    """

    @pytest.mark.tripwire
    def test_a_debt_whose_validate_is_done_is_not_reported(self, tmp_path: Path) -> None:
        (tmp_path / "scripts").mkdir()
        for name in ("validate_check.py", "progress_lint.py"):
            (tmp_path / "scripts" / name).write_text(
                (_ROOT / "scripts" / name).read_text(encoding="utf-8"), encoding="utf-8"
            )
        (tmp_path / "progress.yml").write_text(
            yaml.safe_dump(
                {
                    "items": [],
                    "known_debt": [
                        {
                            "id": "DEBT-CLOSED",
                            "stages": {"validate": "done"},
                            "closing_check": "echo 'CLOSEABLE it happened'",
                        },
                        {
                            "id": "DEBT-OPEN",
                            "stages": {"validate": "partial"},
                            "closing_check": "echo 'OPEN not yet'",
                        },
                    ],
                    "current": {},
                }
            ),
            encoding="utf-8",
        )

        out = subprocess.run(
            [sys.executable, str(tmp_path / "scripts" / "validate_check.py")],
            cwd=tmp_path, capture_output=True, text=True, timeout=120,
        )

        assert "DEBT-OPEN" in out.stdout, (
            f"an open debt vanished from the report:\n{out.stdout}\n{out.stderr}"
        )
        assert "DEBT-CLOSED" not in out.stdout, (
            "a debt whose validate is DONE is still listed as outstanding, so the "
            f"report carries a CLOSEABLE nothing can ever clear:\n{out.stdout}"
        )

    def test_a_debt_with_no_stages_at_all_is_still_reported(self, tmp_path: Path) -> None:
        """The default must stay OPEN. Most debts carry no `stages` key, and reading a
        missing stage as `done` would silently empty the report — a far worse failure
        than the one being fixed."""
        (tmp_path / "scripts").mkdir()
        for name in ("validate_check.py", "progress_lint.py"):
            (tmp_path / "scripts" / name).write_text(
                (_ROOT / "scripts" / name).read_text(encoding="utf-8"), encoding="utf-8"
            )
        (tmp_path / "progress.yml").write_text(
            yaml.safe_dump(
                {
                    "items": [],
                    "known_debt": [
                        {"id": "DEBT-NOSTAGES", "closing_check": "echo 'OPEN not yet'"}
                    ],
                    "current": {},
                }
            ),
            encoding="utf-8",
        )

        out = subprocess.run(
            [sys.executable, str(tmp_path / "scripts" / "validate_check.py")],
            cwd=tmp_path, capture_output=True, text=True, timeout=120,
        )

        assert "DEBT-NOSTAGES" in out.stdout, out.stdout
