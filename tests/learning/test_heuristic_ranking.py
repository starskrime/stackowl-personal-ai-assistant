from stackowl.learning.heuristic_ranking import rank_lessons
from stackowl.learning.lesson import LessonHit


def _hit(ref, sim, source="tool_heuristic", evidence=None, mean_quality=None):
    md: dict[str, object] = {}
    if evidence is not None:
        md["evidence_count"] = evidence
    if mean_quality is not None:
        md["mean_quality"] = mean_quality
    return LessonHit(lesson_id=ref, source_type=source, source_ref=ref,
                     content=f"lesson {ref}", similarity=sim, metadata=md)


def test_well_evidenced_high_similarity_ranks_above_low_evidence():
    hits = [
        _hit("a", sim=0.60, evidence=3),
        _hit("b", sim=0.80, evidence=50),
    ]
    ranked = rank_lessons(hits)
    assert ranked[0].source_ref == "b"


def test_non_heuristic_hits_kept_after_heuristics_in_original_order():
    hits = [
        _hit("r1", sim=0.95, source="reflection"),
        _hit("h1", sim=0.50, evidence=10),
        _hit("r2", sim=0.40, source="reflection"),
    ]
    ranked = rank_lessons(hits)
    assert [h.source_ref for h in ranked[1:]] == ["r1", "r2"]
    assert ranked[0].source_ref == "h1"


def test_missing_evidence_metadata_scores_as_similarity_only():
    hits = [_hit("x", sim=0.30, evidence=None), _hit("y", sim=0.40, evidence=None)]
    ranked = rank_lessons(hits)
    assert [h.source_ref for h in ranked] == ["y", "x"]


def test_equal_similarity_prefers_under_observed():
    # With equal similarity (0.70), the lower-evidence hit should rank first
    # because the UCB exploration term sqrt(ln(N)/ev) is larger for small ev.
    hits = [
        _hit("high_ev", sim=0.70, evidence=50),
        _hit("low_ev", sim=0.70, evidence=3),
    ]
    ranked = rank_lessons(hits)
    assert ranked[0].source_ref == "low_ev"


def test_mean_quality_breaks_tie_between_equal_similarity_and_evidence():
    # F-46: mean_quality must feed a DECISION, not just be rendered. With equal
    # similarity AND equal evidence, the higher-mean_quality heuristic gets a
    # larger exploration nudge, so it ranks first. Listed low-quality FIRST so a
    # no-op (stable sort) would keep "low_q" on top and fail this assertion.
    hits = [
        _hit("low_q", sim=0.70, evidence=3, mean_quality=0.10),
        _hit("high_q", sim=0.70, evidence=3, mean_quality=0.90),
    ]
    ranked = rank_lessons(hits)
    assert ranked[0].source_ref == "high_q"


def test_missing_mean_quality_is_byte_identical_to_legacy_full_bonus():
    # Legacy rows (no mean_quality key) must score exactly as before: full
    # exploration bonus. Equal similarity → lower-evidence ranks first, unchanged.
    hits = [
        _hit("high_ev", sim=0.70, evidence=50),
        _hit("low_ev", sim=0.70, evidence=3),
    ]
    ranked = rank_lessons(hits)
    assert ranked[0].source_ref == "low_ev"


def test_zero_mean_quality_collapses_to_similarity_only():
    # A heuristic whose few successes were low quality gets ~no exploration
    # bonus, so a strictly-higher-similarity peer outranks it despite lower
    # evidence would otherwise nudge it up.
    hits = [
        _hit("zero_q_low_ev", sim=0.60, evidence=3, mean_quality=0.0),
        _hit("good_q_high_ev", sim=0.62, evidence=50, mean_quality=0.9),
    ]
    ranked = rank_lessons(hits)
    assert ranked[0].source_ref == "good_q_high_ev"


def test_bool_and_nonpositive_evidence_treated_as_missing():
    # evidence=True (bool) must be rejected by _evidence() → similarity-only.
    # evidence=None (no key) is also similarity-only.
    # Both hits score by similarity alone, so higher similarity wins.
    # If bool were mistakenly treated as evidence=1 it would receive a large
    # UCB exploration bonus and could flip the order — the assertion catches that.
    hits = [
        _hit("bool_ev", sim=0.50, evidence=True),   # bool → similarity-only
        _hit("no_ev", sim=0.40),                     # no evidence key → similarity-only
    ]
    ranked = rank_lessons(hits)
    assert ranked[0].source_ref == "bool_ev"
