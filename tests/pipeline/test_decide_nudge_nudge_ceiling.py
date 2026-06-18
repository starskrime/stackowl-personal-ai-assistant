from stackowl.pipeline.persistence import PERSISTENCE_DIRECTIVE
from stackowl.pipeline.supervisor import MAX_TURN_NUDGES, decide_nudge


def _giveup_calls(n):
    return [{"name": f"tool{i}", "args": {}} for i in range(n)]


def test_ceiling_fires_despite_continuous_escalation():
    nudge_budget = 2
    calls_at_last_nudge = None
    issued = 0
    for round_i in range(MAX_TURN_NUDGES + 3):
        directive, nudge_budget, calls_at_last_nudge = decide_nudge(
            judge_directive=PERSISTENCE_DIRECTIVE,
            all_calls=_giveup_calls(round_i + 1),   # always escalating
            draft="not done yet",
            nudge_budget=nudge_budget,
            calls_at_last_nudge=calls_at_last_nudge,
            nudges_issued=issued,
        )
        if directive is not None:
            issued += 1
    assert issued == MAX_TURN_NUDGES, f"expected ceiling at {MAX_TURN_NUDGES}, got {issued}"


def test_below_ceiling_escalation_still_waives_budget_cost():
    directive, new_budget, _ = decide_nudge(
        judge_directive=PERSISTENCE_DIRECTIVE,
        all_calls=_giveup_calls(5),
        draft="x",
        nudge_budget=2,
        calls_at_last_nudge=4,
        nudges_issued=0,
    )
    assert directive is not None
    assert new_budget == 2


def test_ceiling_defaults_preserve_prior_behavior():
    directive, _, _ = decide_nudge(
        judge_directive=PERSISTENCE_DIRECTIVE,
        all_calls=_giveup_calls(1),
        draft="x",
        nudge_budget=2,
        calls_at_last_nudge=None,
    )
    assert directive is not None
