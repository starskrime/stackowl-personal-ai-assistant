from stackowl.memory.outcome_store import TaskOutcome
from stackowl.owls.dna_attribution import _filter_scored_outcomes


def _outcome(**overrides):
    defaults = dict(
        outcome_id=1, trace_id="t1", session_id="s1", owl_name="secretary", channel="telegram",
        success=True, latency_ms=100.0, tool_call_count=1, failure_class=None,
        quality_score=0.8, step_durations={}, input_text="hi", response_text="hello",
        captured_at=0.0, scored_at=0.0, dna_snapshot={"trait": 0.5}, approach_rating=None,
    )
    defaults.update(overrides)
    return TaskOutcome(**defaults)


def test_negative_approach_rating_excluded_from_dna_attribution():
    disliked = _outcome(trace_id="t-disliked", approach_rating="negative")
    liked = _outcome(trace_id="t-liked", approach_rating="positive")
    unrated = _outcome(trace_id="t-unrated", approach_rating=None)

    scored = _filter_scored_outcomes([disliked, liked, unrated])

    trace_ids = {o.trace_id for o in scored}
    assert "t-disliked" not in trace_ids
    assert "t-liked" in trace_ids
    assert "t-unrated" in trace_ids  # unrated outcomes keep today's behavior unchanged
