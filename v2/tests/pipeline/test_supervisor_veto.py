from stackowl.pipeline.persistence import PERSISTENCE_DIRECTIVE
from stackowl.pipeline.supervisor import apply_structural_veto

LYING_CALLS = [{"name": "browser_browse", "failed": True}]  # tool failed, nothing succeeded


def test_veto_overrides_lying_delivered_on_giveup():
    # Judge said DELIVERED (None directive) but structurally it's a give-up + trivial draft.
    out = apply_structural_veto(judge_directive=None, all_calls=LYING_CALLS, draft="...")
    assert out == PERSISTENCE_DIRECTIVE  # veto fires


def test_no_veto_when_draft_substantive():
    out = apply_structural_veto(judge_directive=None, all_calls=LYING_CALLS,
                                draft="The capital of France is Paris.")
    assert out is None  # substantive answer -> not a give-up


def test_no_veto_when_a_tool_succeeded():
    calls = [{"name": "x", "failed": True}, {"name": "y", "failed": False}]
    assert apply_structural_veto(judge_directive=None, all_calls=calls, draft="...") is None


def test_judge_directive_passes_through_when_set():
    # If the judge itself flagged a give-up, keep its directive (no double-injection).
    out = apply_structural_veto(judge_directive=PERSISTENCE_DIRECTIVE, all_calls=LYING_CALLS, draft="...")
    assert out == PERSISTENCE_DIRECTIVE
