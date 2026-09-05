"""The one place that turns a :class:`BudgetBreach` into what the user is told.

WHY THIS MODULE EXISTS. `execute.py` built a fixed sentence — "I ran out of steps
for this turn" — for every default-backstop stop, while the `BudgetBreach` it was
handling already carried `cap`, `limit` and `actual`. The governor knew which cap
fired; the note asserted one independently. Two copies of one rule (CLAUDE.md
shape #3), and the cost was not a wrong log line.

MEASURED 2026-09-05: of that day's five breaches FOUR were `tokens`; across every
kept log the split is 42 `steps` / 27 `tokens`. All of them said "steps". The
operator, reading his own platform's output, reported that "almost every request
runs out of steps" and asked what was wrong with the PLANNER — which was not the
problem. A message that names the wrong cause does not merely fail to help; it
actively directs the next person's attention at the wrong subsystem, and it did.

The two caps are also not interchangeable, and they are COINCIDENT — which is
why guessing between them was never safe. Measured across the last 400 substantive
rounds the median is 24,811 input tokens, so a 500,000-token cap crosses at ~20.15
rounds against a 20-step cap: a 1% margin. Which cap fires is decided by a turn's
incidental mix of large and small calls, not by design, and that is exactly why
the historical split is 42 `steps` / 27 `tokens` rather than one dominating. A
fixed sentence could therefore only ever be right about half the time.

(Most of that round is waste: consecutive rounds inside one turn measured
30,952 -> 31,086 -> 31,255, deltas of +134 and +169, so ~99.5% is unchanged prefix
re-sent because nothing caches — 0 cached tokens across 708M input tokens, every
provider, all history, with the cost tracker recording "not_reported". That is the
reason turns die; this module only stops them lying about which limit did it.)

"Steps" points at the planner; "tokens" points at prompt size. Different repairs.

FAILS TOWARD SAYING LESS. An unrecognised cap gets a truthful generic sentence
rather than a guess, because the failure this module exists to prevent is
confident misattribution, not silence.
"""

from __future__ import annotations

from stackowl.exceptions import BudgetBreach

#: Cause -> what the user is told. Kept as DATA rather than an if-tree so a new
#: cap cannot be added without someone stating what it says — the same argument
#: `RECOVERY_FOR_CAUSE` makes in `providers/_resilient_round.py`.
_NOTE_FOR_CAP: dict[str, str] = {
    "steps": (
        "I ran out of steps for this turn before I could finish — I hit the limit "
        "on how many actions one turn may take."
    ),
    "tokens": (
        "this turn reached its token budget before I could finish — the "
        "conversation and tool results grew past what one turn may spend."
    ),
    "time": "this turn ran out of time before I could finish.",
    "cost": "this turn reached its spending limit before I could finish.",
}

_UNKNOWN = "this turn reached a budget limit before I could finish."

#: EVERY note ends with a next step. A notice that only reports a failure leaves
#: the person stuck, which is the whole reason the silent stop was a defect —
#: `tests/pipeline/test_a_budget_stop_is_never_silent.py` pins that requirement.
#: This offer is TRUE where the old one was not: budgets are per-turn, so asking
#: again really does start with a full budget. "I'll pick up from here" claimed a
#: continuation that nothing implements.
_NEXT_STEP = " Ask again with a narrower scope and I'll start with a fresh budget."


def stop_note_for(breach: BudgetBreach) -> str:
    """The user-facing sentence for ``breach``, naming the cap that actually fired.

    NO CONTINUATION PROMISE. The previous wording ended "Ask me to continue and
    I'll pick up from here." Nothing implements that: `budget_capped=True` makes
    the corrective path count the turn as floored (`retry_actuator.py:205`
    reports `corrected: false`) and no continuation task is enqueued anywhere. A
    promise the platform cannot keep is the same class of defect as naming the
    wrong cap — an assertion about behaviour that the code does not have.
    """
    return f"[stopped: {_NOTE_FOR_CAP.get(breach.cap, _UNKNOWN)}{_NEXT_STEP}]"
