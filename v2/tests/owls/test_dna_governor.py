from stackowl.owls.dna import OwlDNA
from stackowl.owls.dna_governor import bound_dna


def test_max_delta_caps_a_big_move():
    out = bound_dna(OwlDNA(curiosity=0.50), OwlDNA(curiosity=0.95))
    assert abs(out.curiosity - 0.55) < 1e-9          # capped to +MAX_DELTA (0.05)


def test_envelope_clamps_to_neutral_band():
    out = bound_dna(OwlDNA(curiosity=0.69), OwlDNA(curiosity=0.74))
    assert out.curiosity <= 0.70 + 1e-9               # ENVELOPE 0.2 around 0.5


def test_safety_floor_on_judgment_traits():
    assert bound_dna(OwlDNA(challenge_level=0.30), OwlDNA(challenge_level=0.26)).challenge_level >= 0.25
    assert bound_dna(OwlDNA(precision=0.28), OwlDNA(precision=0.20)).precision >= 0.25


def test_no_change_is_identity():
    assert bound_dna(OwlDNA(verbosity=0.5), OwlDNA(verbosity=0.5)).verbosity == 0.5


def test_decay_rate_field_untouched():
    assert bound_dna(OwlDNA(decay_rate_per_week=0.05), OwlDNA(decay_rate_per_week=0.9)).decay_rate_per_week == 0.05
