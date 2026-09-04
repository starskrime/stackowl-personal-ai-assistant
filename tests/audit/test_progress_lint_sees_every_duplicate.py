"""The duplicate-key guard must cover the WHOLE file, not just `items:`.

WHY THIS EXISTS, and it is the guard's own defect shape turned on itself.

`scripts/progress_lint.py` was written on 2026-08-08 because a slice of D10.2 work
was written into a key that appeared twice, and `yaml.safe_load` discarded it with no
error. Its docstring states the lesson exactly: "the write happens, the effect does
not, and nothing says so. So it becomes a check."

The check was wired on only some paths. `_item_blocks()` matches `^  - id: `, so it
inspects ITEM blocks and nothing else, at one fixed indent (`^    (\\w+):`). The
`current:` mapping — which holds `stage`, `ESCALATIONS`, and every hand-off note, and
is the single most-written block in the file — was never inspected.

MEASURED 2026-08-21: `progress.yml` carried `ESCALATIONS:` TWICE under `current:`, at
lines 425 and 699. The first is a mapping holding D16.3's whole brainstorm escalation
record — `raised`, `status`, `context` and five question blocks, ~76 lines, including
the note that E2 and E4 remained open. YAML keeps the LAST occurrence, so all of it was
discarded at every load. And `progress_lint.py` printed:

    ✓ progress.yml sound — 112 items, no duplicate keys

A guard that prints a claim it does not test is worse than no guard, because the claim
is believed. That is the same "actuator wired on only some paths" this programme keeps
finding, sitting inside the instrument built to find it.

THE FIX IS STRUCTURAL, not a widened regex. `yaml.compose()` builds the node tree
BEFORE duplicate keys are merged away, so every mapping in the document can be checked
exactly, at any depth, with no indent assumptions. That also retires `_NESTED_OK` — it
existed only because "the flat regex cannot tell nesting apart", and a composer can.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "scripts"))

import progress_lint  # noqa: E402


class TestItSeesADuplicateAnywhere:
    def test_a_duplicate_under_current_is_reported(self) -> None:
        """THE LIVE CASE. Two `ESCALATIONS:` under `current:` — exactly what the real
        file carried while the linter called it sound."""
        text = (
            "current:\n"
            "  item: D04.1\n"
            "  ESCALATIONS:\n"
            "    raised: 2026-08-20\n"
            "  stage: brainstorm\n"
            "  ESCALATIONS:\n"
            "    - question: something\n"
            "items: []\n"
        )

        problems = progress_lint.duplicate_key_problems(text)

        assert any("ESCALATIONS" in p for p in problems), problems

    def test_it_still_catches_the_item_case_it_was_built_for(self) -> None:
        """The 2026-08-08 D10.2 defect. Widening coverage must not lose it."""
        text = (
            "items:\n"
            "  - id: D10.2\n"
            "    changes: []\n"
            "    decisions: []\n"
            "    changes:\n"
            "      - the write that vanished\n"
        )

        problems = progress_lint.duplicate_key_problems(text)

        assert any("changes" in p and "D10.2" in p for p in problems), problems

    def test_a_duplicate_at_the_TOP_level_is_reported(self) -> None:
        """`current:` itself could be written twice; nothing checks that today."""
        text = "current:\n  item: A\nitems: []\ncurrent:\n  item: B\n"

        assert any("current" in p for p in progress_lint.duplicate_key_problems(text))

    def test_repeated_keys_in_SIBLING_mappings_are_not_duplicates(self) -> None:
        """The false positive `_NESTED_OK` existed to suppress. A composer knows the
        difference, so `status` twice in two different items is fine."""
        text = (
            "items:\n"
            "  - id: A\n"
            "    status: done\n"
            "  - id: B\n"
            "    status: done\n"
        )

        assert progress_lint.duplicate_key_problems(text) == []

    def test_the_message_names_the_consequence(self) -> None:
        """A linter line nobody understands gets ignored. It must say that the earlier
        write is DISCARDED, which is the part that costs work."""
        text = "current:\n  a: 1\n  a: 2\n"

        (problem,) = progress_lint.duplicate_key_problems(text)

        assert "discard" in problem.lower()


class TestTheRealFileIsSound:
    def test_progress_yml_has_no_duplicate_keys_anywhere(self) -> None:
        """Runs the widened check against the real file. This is the assertion the
        printed success line has always claimed and never made."""
        text = (_ROOT / "progress.yml").read_text(encoding="utf-8")

        problems = progress_lint.duplicate_key_problems(text)

        assert problems == [], (
            "keys that YAML will silently collapse, discarding the earlier write:\n  "
            + "\n  ".join(problems)
        )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))


# --------------------------------------------------------------------------
# The state of record can also diverge from itself. Added 2026-09-04, after
# D04.4, D12.8 and N01 — the three most recently worked items — were found with
# a full narrative record under `current` and every one of their seven stages
# still reading `not_started`.
# --------------------------------------------------------------------------


def _data(stages: dict[str, str]) -> dict:
    return {
        "current": {"D12_8_2026_09_03_the_untrusted_marker_reached_ONE_tool": {}},
        "items": [{"id": "D12.8", "stages": stages}],
    }


_ALL_DONE = {
    "brainstorm": "done", "architect": "done", "implement": "done",
    "cleanup": "done", "test": "done", "validate": "done", "document": "done",
}


def test_a_worked_item_whose_stages_never_moved_is_a_problem() -> None:
    """The loop picks its next item FROM these stages.

    A finished item still reading `not_started` is picked again, and the count
    of what remains is wrong in the direction that hides work.
    """
    stages = dict.fromkeys(_ALL_DONE, "not_started")
    problems = progress_lint.stale_stage_problems(_data(stages))
    assert len(problems) == 1
    assert "D12.8" in problems[0]


def test_it_catches_a_HALF_updated_item_too() -> None:
    """The first version of this check asserted 'every stage is not_started'.

    It was too weak in a way it demonstrated immediately: a repair script
    rewrote ONE stage line per item — the others use aligned padding its regex
    missed — and the check went green over a state still recording implement and
    test as never started. A guard that passes on a known-wrong state is worse
    than no guard, because it becomes the reason nobody looks.
    """
    stages = dict.fromkeys(_ALL_DONE, "not_started")
    stages["brainstorm"] = "done"  # exactly the half-update that slipped through
    problems = progress_lint.stale_stage_problems(_data(stages))
    assert len(problems) == 1, "a half-updated item must still be caught"
    assert "implement" in problems[0]


def test_a_genuinely_finished_item_is_silent() -> None:
    """Vacuity control: the check must not fire on the state it wants."""
    assert progress_lint.stale_stage_problems(_data(dict(_ALL_DONE))) == []


def test_an_item_with_NO_record_is_never_flagged() -> None:
    """Most items have no `current` record and are simply not started yet."""
    data = {"current": {}, "items": [{"id": "D12.8",
                                      "stages": dict.fromkeys(_ALL_DONE, "not_started")}]}
    assert progress_lint.stale_stage_problems(data) == []


def test_the_id_prefix_does_not_match_a_LONGER_id() -> None:
    """`D01.1` must not claim `D01_10`'s record — an off-by-one that would mark
    a genuinely unstarted item as worked, which is the failure this check exists
    to prevent, pointed the other way."""
    data = {
        "current": {"D01_10_2026_09_04_some_other_item": {}},
        "items": [{"id": "D01.1", "stages": dict.fromkeys(_ALL_DONE, "not_started")}],
    }
    assert progress_lint.stale_stage_problems(data) == []


# --------------------------------------------------------------------------
# The cardinal rule needed an enforcer. Added 2026-09-04, after D03.4 and D11.2
# — both P1 — were found carrying `validate: done` with no evidence of any kind.
# --------------------------------------------------------------------------


def _validated(**fields: object) -> dict:
    item = {"id": "D03.4", "stages": dict(_ALL_DONE)}
    item.update(fields)
    return {"current": {}, "items": [item]}


def test_validate_done_with_no_evidence_is_a_problem() -> None:
    """Both offenders turned out to be TRUE when re-measured, which is the point:
    the claims were right and unverifiable by inspection, so the only way to trust
    them was to do the work again. Evidence that must be re-derived is not a
    state of record."""
    problems = progress_lint.unevidenced_validate_problems(_validated())
    assert len(problems) == 1
    assert "D03.4" in problems[0]


@pytest.mark.parametrize(
    "field", ["doc", "validate_result", "changes", "notes", "decisions"]
)
def test_any_ONE_piece_of_evidence_satisfies_it(field: str) -> None:
    """Deliberately permissive: the check asks for something a reader can follow,
    not for a particular ceremony. A guard that demanded one exact field would be
    routed around by writing that field empty."""
    assert progress_lint.unevidenced_validate_problems(_validated(**{field: "x"})) == []


def test_an_EMPTY_evidence_field_does_not_count() -> None:
    """`changes: []` and `notes: null` are what the two offenders actually had."""
    data = _validated(changes=[], notes=None, decisions=[], doc=None)
    assert len(progress_lint.unevidenced_validate_problems(data)) == 1


def test_a_current_record_counts_as_evidence() -> None:
    data = _validated()
    data["current"] = {"D03_4_2026_09_04_something_was_measured": {}}
    assert progress_lint.unevidenced_validate_problems(data) == []


def test_an_item_still_being_validated_is_not_asked_for_evidence_yet() -> None:
    """Vacuity control: the check must only speak about CLAIMS, not open work.

    `document` is set to not_started here deliberately. When this test was
    written only `validate` was a claim stage, so `dict(_ALL_DONE)` was harmless;
    once `document` joined on 2026-09-04 that fixture asserted a SECOND
    unevidenced claim and the test failed — correctly. The guard was right and the
    fixture's assumption had expired, which is the same drift a test double
    suffers when the thing it doubles grows a field.
    """
    stages = dict(_ALL_DONE)
    stages["validate"] = "partial"
    stages["document"] = "not_started"
    assert progress_lint.unevidenced_validate_problems(
        {"current": {}, "items": [{"id": "D03.4", "stages": stages}]}
    ) == []


# --------------------------------------------------------------------------
# The claim rule is the SAME rule for both claim stages. Extended 2026-09-04
# after D03.5 was found reading `document: done` with `doc: null` — the shape
# the validate-only check could not see.
# --------------------------------------------------------------------------


def _claiming(stage: str, **fields: object) -> dict:
    stages = dict.fromkeys(_ALL_DONE, "not_started")
    stages[stage] = "done"
    item = {"id": "D03.5", "stages": stages}
    item.update(fields)
    return {"current": {}, "items": [item]}


@pytest.mark.parametrize("stage", ["validate", "document"])
def test_either_claim_stage_needs_evidence(stage: str) -> None:
    problems = progress_lint.unevidenced_validate_problems(_claiming(stage))
    assert len(problems) == 1
    assert stage in problems[0]


@pytest.mark.parametrize("stage", ["validate", "document"])
def test_either_claim_stage_is_satisfied_by_a_doc(stage: str) -> None:
    assert progress_lint.unevidenced_validate_problems(
        _claiming(stage, doc="docs/reference-mapping/designs/D03.5.md")
    ) == []


def test_implement_done_is_NOT_a_claim_stage() -> None:
    """Deliberately excluded: `implement: done` is checked by the tests. Only
    validate and document assert something about the world that nothing else
    verifies, which is why only those two must leave a record."""
    stages = dict.fromkeys(_ALL_DONE, "not_started")
    stages["implement"] = "done"
    data = {"current": {}, "items": [{"id": "D03.5", "stages": stages}]}
    assert progress_lint.unevidenced_validate_problems(data) == []


def test_the_live_file_has_no_unevidenced_claim() -> None:
    """The measurement that made this extension a hardening rather than a fix.

    Three predicates were tried while investigating: "document: done with no doc
    path" found 15, "…and no current record" found 10, and the function's OWN
    evidence set — doc, record, validate_result, changes, notes, decisions —
    found ZERO. The first two were narrower than the rule already in force, and
    each would have condemned items that carry evidence in a field it forgot to
    look at.
    """
    import yaml

    data = yaml.safe_load(
        (Path(__file__).resolve().parents[2] / "progress.yml").read_text()
    )
    assert progress_lint.unevidenced_validate_problems(data) == []
