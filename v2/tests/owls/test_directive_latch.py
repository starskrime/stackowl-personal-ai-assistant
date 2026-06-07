from stackowl.owls.directive_latch import (
    DirectiveLatch, HIGH_ENTER, HIGH_EXIT, LOW_ENTER, LOW_EXIT,
)
from stackowl.owls.evolution_limits import MAX_DELTA


def test_gap_exceeds_max_delta():
    assert (HIGH_ENTER - HIGH_EXIT) > MAX_DELTA
    assert (LOW_EXIT - LOW_ENTER) > MAX_DELTA


def test_high_lazy_seed_matches_plain_threshold():
    lt = DirectiveLatch()
    assert lt.high_state("o", "challenge_level", 0.72) is True
    assert lt.high_state("o2", "challenge_level", 0.50) is False


def test_high_holds_in_deadband_then_exits():
    lt = DirectiveLatch()
    assert lt.high_state("o", "x", 0.72) is True    # enter
    assert lt.high_state("o", "x", 0.66) is True    # hold (0.60..0.70)
    assert lt.high_state("o", "x", 0.59) is False   # exit (<0.60)
    assert lt.high_state("o", "x", 0.66) is False   # stays off in deadband (was off)


def test_low_direction_independent():
    lt = DirectiveLatch()
    assert lt.low_state("o", "formality", 0.28) is True   # enter low
    assert lt.low_state("o", "formality", 0.36) is True   # hold (0.30..0.40)
    assert lt.low_state("o", "formality", 0.41) is False  # exit (>0.40)


def test_reset_owl_clears():
    lt = DirectiveLatch()
    lt.high_state("o", "x", 0.72)
    lt.reset_owl("o")
    assert lt.high_state("o", "x", 0.66) is False  # cold-seed at 0.66 (deadband) -> off (not held True)


def test_singleton_exists_and_clears():
    from stackowl.owls.directive_latch import DIRECTIVE_LATCH
    DIRECTIVE_LATCH.clear_all()
    DIRECTIVE_LATCH.reset_owl("nobody")  # no crash
