"""A stage recorded `partial` must say what evidence would close it.

WHY THIS EXISTS, and it is this programme's own rule left unenforced for one word.

`item-loop/SKILL.md` states the honest-validate rule outright: "A check you could
not evidence stays OPEN **with the query that would close it**." The escalation
queue got that rule made EXECUTABLE — every entry carries a `premise_check`, a
one-liner printing HOLDS or EXPIRED, and `scripts/escalation_check.py` re-runs all
51 at the start of every loop. The same file's own words say why: "an escalation got
written once, with a measurement, and was never looked at again — so its premise aged
silently."

Validate stages got none of it. MEASURED 2026-09-06: ten stages across the whole file
are `partial`, every one of them `validate`, and **zero items carry any structured
field naming what would close them**. Three had a closing query buried in English
inside `changes:`; the other SEVEN had nothing at all. Those seven are not open
questions, they are dead ends — nobody can close them because nobody wrote down what
closing would look like.

`progress_lint` already refuses a `done` claim with no evidence behind it, on exactly
this reasoning: "a claim nobody can check is not a state of record." A `partial` is
also a claim — the claim that evidence is not yet available — and it was exempt.

AND ONE OF THEM COULD NEVER HAVE BEEN CLOSED AT ALL. D11.3's frame renders as text
returned to the model and logged nothing, so no volume of production traffic could
have produced evidence. That is D08.1's DEBUG-only evidence line one step worse: not
a line at the wrong level, no line in existence. The rule that "if you cannot write
the check, the premise is too vague — fix the premise" is what caught it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "scripts"))

import progress_lint  # noqa: E402


def _doc(stage_value: str, *, closing_check: str | None) -> dict:
    item: dict = {
        "id": "D99.9",
        "stages": {
            "brainstorm": "done", "architect": "done", "implement": "done",
            "cleanup": "done", "test": "done", "validate": stage_value,
            "document": "done",
        },
        "doc": "docs/reference-mapping/designs/D99.9.md",
    }
    if closing_check is not None:
        item["closing_check"] = closing_check
    return {"current": {}, "items": [item]}


class TestTheRuleItself:
    def test_a_partial_with_no_closing_check_is_a_problem(self) -> None:
        """THE SEVEN DEAD ENDS. A stage that says "I could not evidence this" and
        does not say what would, can never be advanced by anyone."""
        problems = progress_lint.partial_without_closing_check_problems(
            _doc("partial", closing_check=None)
        )

        assert problems, "a partial with no closing check was accepted"
        assert "D99.9" in problems[0]
        assert "validate" in problems[0]

    def test_a_partial_WITH_a_closing_check_is_accepted(self) -> None:
        """The control. Without this the rule could be satisfied by never using
        `partial` at all, which would push the same claims into worse words."""
        assert not progress_lint.partial_without_closing_check_problems(
            _doc("partial", closing_check="echo OPEN")
        )

    def test_an_EMPTY_closing_check_does_not_count(self) -> None:
        """`closing_check: ""` is the form this rule would be defeated by first —
        the field present, the obligation not met."""
        assert progress_lint.partial_without_closing_check_problems(
            _doc("partial", closing_check="   ")
        )

    def test_a_DONE_stage_needs_no_closing_check(self) -> None:
        """Scope control. `done` is already governed by
        `unevidenced_validate_problems`; two rules over one stage value would be
        the duplication this codebase keeps paying for."""
        assert not progress_lint.partial_without_closing_check_problems(
            _doc("done", closing_check=None)
        )

    def test_it_covers_EVERY_stage_not_just_validate(self) -> None:
        """THE MISTAKE THE SIBLING RULE ALREADY MADE ONCE. `unevidenced_validate_
        problems` looked only at `validate` and so could not see the identical
        defect on `document`; its own docstring records the extension. All ten
        partials today are `validate`, and writing the rule to that fact would
        rebuild the same blind spot on purpose."""
        doc = _doc("done", closing_check=None)
        doc["items"][0]["stages"]["architect"] = "partial"

        problems = progress_lint.partial_without_closing_check_problems(doc)

        assert problems and "architect" in problems[0]


class TestTheLiveFile:
    def test_the_real_progress_yml_has_no_unclosable_partial(self) -> None:
        """The rule against the actual state of record — the assertion that makes
        this a gate rather than a unit test of a pure function."""
        import yaml

        data = yaml.safe_load((_ROOT / "progress.yml").read_text(encoding="utf-8"))

        assert not progress_lint.partial_without_closing_check_problems(data)

    def test_every_closing_check_is_RUNNABLE(self) -> None:
        """A check that cannot execute is prose with a colon in front of it.

        This is the lesson from `premise_check`, which is verified the same way by
        `escalation_check.py` refusing to count an entry it could not run.
        """
        import shlex

        import yaml

        data = yaml.safe_load((_ROOT / "progress.yml").read_text(encoding="utf-8"))
        broken: list[str] = []
        for item in data.get("items", []):
            check = (item.get("closing_check") or "").strip()
            if not check:
                continue
            try:
                shlex.split(check)
            except ValueError as exc:
                broken.append(f"{item.get('id')}: unparseable — {exc}")
        assert not broken, "\n  ".join(broken)


class TestAClosedStageDropsItsCheck:
    """A `closing_check` outlives the `partial` it was written for unless something
    says otherwise, and then it is a field with no reader — this codebase's
    most-recorded shape, in the very file that records it.

    `validate_check.py` only iterates items carrying a partial stage, so a check left
    behind on a closed item is never run and never seen. It reads as live evidence to
    anyone browsing the record and is not: the thing it interrogates may have been
    deleted the day after the stage closed.
    """

    def test_a_check_left_on_a_fully_closed_item_is_flagged(self) -> None:
        doc = _doc("done", closing_check="echo CLOSEABLE")

        problems = progress_lint.stale_closing_check_problems(doc)

        assert problems and "D99.9" in problems[0]

    def test_a_check_on_a_still_partial_item_is_fine(self) -> None:
        """The control — the field is REQUIRED there, so flagging it would make the
        two rules contradict each other."""
        assert not progress_lint.stale_closing_check_problems(
            _doc("partial", closing_check="echo OPEN")
        )

    def test_an_item_with_no_check_at_all_is_fine(self) -> None:
        assert not progress_lint.stale_closing_check_problems(
            _doc("done", closing_check=None)
        )

    def test_the_live_file_carries_no_stale_check(self) -> None:
        import yaml

        data = yaml.safe_load((_ROOT / "progress.yml").read_text(encoding="utf-8"))

        assert not progress_lint.stale_closing_check_problems(data)


class TestTheMechanismIsActuallyReached:
    """Built-but-not-wired is this repo's most expensive recurring defect, and a
    reporting script is its most natural habitat: nothing fails when it stops being
    run. `escalation_check.py` is reached only because `item-loop/SKILL.md` tells the
    loop to run it — there is no other caller anywhere in the tree."""

    @pytest.mark.tripwire
    def test_the_loop_skill_runs_validate_check(self) -> None:
        skill = (_ROOT / ".claude" / "skills" / "item-loop" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        assert "scripts/validate_check.py" in skill, (
            "nothing invokes validate_check.py — the closing checks would be written "
            "and never re-read, which is the defect it was built to end"
        )

    @pytest.mark.parametrize(
        "rule",
        ["partial_without_closing_check_problems", "stale_closing_check_problems"],
    )
    @pytest.mark.tripwire
    def test_progress_lint_calls_the_rule(self, rule: str) -> None:
        """A rule is only a gate if `main()` runs it. A function defined and never
        called would leave every future partial unchecked while the file still printed
        'progress.yml sound'.

        PARAMETRISED over BOTH rules rather than naming one. The first version asserted
        only the partial rule, so its mirror could have shipped unwired — which is the
        same one-case-short shape these rules exist to catch, in the test that guards
        them."""
        src = (_ROOT / "scripts" / "progress_lint.py").read_text(encoding="utf-8")
        assert f"problems.extend({rule}(data))" in src, (
            f"progress_lint defines {rule} but never runs it"
        )


@pytest.mark.tripwire
def test_a_commented_closing_check_keeps_its_line_breaks() -> None:
    """A folded YAML scalar joins its lines, so a leading `#` comments out the whole check.

    MEASURED 2026-09-07, on a check I had just written. `closing_check: >-` FOLDS every
    line into one, so a check that opens with an explanatory `#` comment — the house style,
    and most of these do — becomes a single line beginning with `#`. The shell then runs
    nothing, `validate_check.py` reports `<no output>`, and the item is unverifiable rather
    than open. Silent by construction: the YAML is valid, `progress_lint` is satisfied, and
    the record LOOKS like it carries a runnable check.

    `|-` preserves the breaks and is the only correct style for a commented check.

    THE SYMPTOM IS NOT A MISSING NEWLINE, and the first version of this guard tested for
    one and did not fire. YAML's folded style keeps a line break before any MORE-indented
    line, so a check with an indented `if` body still contains newlines — it is only the
    comment lines and the first commands that get run together. The real signature is a
    line that OPENS with `#` and has swallowed executable code, so this looks for shell
    structure inside a comment: `$(`, `&&`, or `; then`. A correctly written comment line
    is prose and contains none of them.
    """
    import yaml

    from progress_lint import entries_with_closing_checks

    data = yaml.safe_load((_ROOT / "progress.yml").read_text(encoding="utf-8"))
    folded: list[str] = []
    seen = 0
    swallowed = ("$(", "&&", "; then")
    for ident, raw in entries_with_closing_checks(data):
        text = str(raw)
        if not text.lstrip().startswith("#"):
            continue
        seen += 1
        for line in text.splitlines():
            bare = line.strip()
            if bare.startswith("#") and any(tok in bare for tok in swallowed):
                folded.append(f"{ident}: a `#` line swallowed shell — {bare[:70]}")
                break
    assert seen >= 3, f"only {seen} commented check(s) seen — the rule has gone blind"
    assert not folded, (
        "these run nothing at all, because a folded scalar turned the whole check into "
        "one comment — use `|-`:\n  " + "\n  ".join(folded)
    )
