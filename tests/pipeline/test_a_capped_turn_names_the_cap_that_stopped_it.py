"""A turn that stops must name the budget that actually stopped it.

MEASURED 2026-09-05, and this is why it matters more than a wording nit. The
operator reported "almost every request runs out of steps" and asked what was
wrong with the PLANNER. The planner was not the problem. Of the five budget
breaches that day, FOUR were `tokens`; across all logs the split is 42 steps / 27
tokens. Every one of them told the user the same fixed sentence — *"I ran out of
steps for this turn"* — because `execute.py` built a hardcoded string while the
`BudgetBreach` in the same scope already carried `cap`, `limit` and `actual`.

THE ROOT CAUSE IS TWO COPIES OF ONE RULE. The governor decides which cap fired;
the note asserts one independently. That is CLAUDE.md shape #3, and its cost here
was not a wrong log line — it was a wrong DIAGNOSIS held by the person who owns
the system, pointed at the wrong subsystem, for as long as the string has existed.

WHY THE DISTINCTION IS NOT COSMETIC. The two caps are COINCIDENT: across the last
400 substantive rounds the median is 24,811 input tokens, so a 500,000-token cap
crosses at ~20.15 rounds against a 20-step cap — a 1% margin. Which cap fires is
decided by a turn's incidental mix of large and small calls, so a FIXED sentence
could only ever be right about half the time, and the record agrees (42 `steps` /
27 `tokens`).

That number is a correction. This file first said the step cap "can never fire",
generalised from one trace's ~31,000-token rounds; the escalation's own
premise_check refuted it within the same item. The fix is unaffected — a derived
note is right either way — but the claim was not, and an over-claim in a docstring
outlives the conversation that produced it.

"Steps" sends you to the planner; "tokens" sends you to prompt size.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from stackowl.exceptions import BudgetBreach
from stackowl.pipeline.budget.stop_note import stop_note_for

_EXECUTE = pathlib.Path(__file__).resolve().parents[2] / "src" / "stackowl" / "pipeline" / "steps" / "execute.py"


def _breach(cap: str) -> BudgetBreach:
    return BudgetBreach(cap, 500000.0, 584905.0)


def test_a_token_breach_does_not_blame_steps() -> None:
    """The exact defect that misdirected the operator."""
    note = stop_note_for(_breach("tokens"))

    assert "step" not in note.lower(), (
        f"a TOKEN breach still says steps: {note!r}. This is the sentence that "
        "sent the operator to the planner while the real cause was prompt size."
    )
    assert "token" in note.lower()


def test_a_step_breach_still_says_steps() -> None:
    """The positive control — without it, 'never say steps' would pass."""
    note = stop_note_for(_breach("steps"))
    assert "step" in note.lower()


@pytest.mark.parametrize("cap", ["steps", "tokens", "time", "cost"])
def test_every_cap_produces_a_DISTINCT_and_honest_note(cap: str) -> None:
    note = stop_note_for(_breach(cap))
    assert note.startswith("[stopped:") and note.endswith("]")
    assert len(note) > 20


def test_the_four_caps_do_not_share_a_sentence() -> None:
    """If two caps render identically, the note has not actually been derived —
    it is a constant wearing a function's clothes."""
    notes = {c: stop_note_for(_breach(c)) for c in ("steps", "tokens", "time", "cost")}
    assert len(set(notes.values())) == 4, f"caps collapsed onto one message: {notes}"


def test_an_unknown_cap_is_honest_rather_than_guessing() -> None:
    """A cap this function has never heard of must not be reported as steps.
    Fails toward saying less, never toward saying something false."""
    note = stop_note_for(_breach("some_future_cap"))
    assert "step" not in note.lower()
    assert "budget" in note.lower() or "limit" in note.lower()


def test_the_note_does_not_promise_a_continuation_that_does_not_exist() -> None:
    """MEASURED: `budget_capped=True` makes the corrective run count as floored
    (`retry_actuator.py:205`, `corrected: false`) and no continuation task is
    enqueued anywhere. The old note said "Ask me to continue and I'll pick up
    from here" — an offer the platform cannot honour. Saying it is a promise;
    a promise the code cannot keep is the same defect as the wrong cap name.
    """
    note = stop_note_for(_breach("tokens"))
    assert "pick up from here" not in note.lower(), (
        "the note still promises to resume from where it stopped, which nothing "
        "implements — there is no continuation task and the corrective path "
        "treats a budget-capped turn as floored"
    )


@pytest.mark.tripwire
def test_no_hardcoded_cap_sentence_survives_in_execute() -> None:
    """ONE SOURCE. The whole defect was a second copy of the governor's answer,
    so the guard is that the copy cannot come back.

    Cross-cutting by nature: whoever adds the next stop path will be editing
    execute.py, and nothing about that edit looks related to this test.
    """
    text = _EXECUTE.read_text(encoding="utf-8")
    code = "\n".join(
        line for line in text.splitlines()
        if not line.lstrip().startswith("#")
    )
    offenders = re.findall(r'"[^"]*ran out of steps[^"]*"', code)
    assert not offenders, (
        f"execute.py hardcodes a cap sentence again: {offenders}. "
        "The BudgetBreach in scope already carries `cap` — ask it, do not assert. "
        "Two copies of that answer is what told the operator 'steps' on a turn "
        "that died on tokens."
    )
