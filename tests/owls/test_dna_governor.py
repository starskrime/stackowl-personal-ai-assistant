from stackowl.owls.dna import OwlDNA
from stackowl.owls.dna_governor import bound_dna


def test_max_delta_caps_a_big_move():
    out = bound_dna(OwlDNA(curiosity=0.50), OwlDNA(curiosity=0.95), OwlDNA())
    assert abs(out.curiosity - 0.55) < 1e-9          # capped to +MAX_DELTA (0.05)


def test_envelope_caps_at_neutral_band_high():
    # band is [0.2, 0.8]; a big proposed move is delta-capped then envelope-capped at 0.8
    out = bound_dna(OwlDNA(curiosity=0.78), OwlDNA(curiosity=0.99), OwlDNA())
    assert abs(out.curiosity - 0.8) < 1e-9            # ENVELOPE 0.3 around 0.5 → hi 0.8


def test_evolution_can_cross_a_directive_threshold():
    # the feature's point: a non-floor trait CAN cross the injector's >0.7 threshold
    out = bound_dna(OwlDNA(curiosity=0.69), OwlDNA(curiosity=0.9), OwlDNA())
    assert out.curiosity > 0.70                       # perceptible: high directive can engage


def test_safety_floor_on_judgment_traits():
    # floor (0.3) is load-bearing: it sits above the envelope low bound (0.2)
    assert bound_dna(OwlDNA(challenge_level=0.32), OwlDNA(challenge_level=0.20), OwlDNA()).challenge_level >= 0.3
    assert bound_dna(OwlDNA(precision=0.31), OwlDNA(precision=0.20), OwlDNA()).precision >= 0.3


def test_non_floor_trait_can_enter_low_band():
    # verbosity (no floor) CAN cross the <0.3 low directive — only judgment traits are floored
    out = bound_dna(OwlDNA(verbosity=0.31), OwlDNA(verbosity=0.1), OwlDNA())
    assert out.verbosity < 0.30


def test_no_change_is_identity():
    assert bound_dna(OwlDNA(verbosity=0.5), OwlDNA(verbosity=0.5), OwlDNA()).verbosity == 0.5


def test_decay_rate_field_untouched():
    assert bound_dna(OwlDNA(decay_rate_per_week=0.05), OwlDNA(decay_rate_per_week=0.9), OwlDNA()).decay_rate_per_week == 0.05


def test_envelope_recenters_on_anchor():
    # authored 0.9 → band [0.6, 1.0]. current 0.85 (already above the legacy 0.8 ceiling),
    # proposed 0.95 → rate-capped to 0.90, which stays in the ANCHORED band [0.6,1.0].
    # Under the OLD neutral envelope [0.2,0.8] this same input clamps DOWN to 0.8 — so the
    # two differ here, genuinely proving the re-anchor.
    out = bound_dna(OwlDNA(challenge_level=0.85), OwlDNA(challenge_level=0.95), OwlDNA(challenge_level=0.9))
    assert abs(out.challenge_level - 0.90) < 1e-9
    # same inputs under a NEUTRAL anchor clamp down to the legacy 0.8 ceiling — proving the difference
    neutral = bound_dna(OwlDNA(challenge_level=0.85), OwlDNA(challenge_level=0.95), OwlDNA())
    assert abs(neutral.challenge_level - 0.8) < 1e-9


def test_anchor_floor_defers_to_author_below_floor():
    # authored precision 0.1 → effective floor min(0.3, 0.1)=0.1; evolution can't go below 0.1 but may sit there
    out = bound_dna(OwlDNA(precision=0.12), OwlDNA(precision=0.0), OwlDNA(precision=0.1))
    assert abs(out.precision - 0.1) < 1e-9


def test_anchor_floor_normal_when_author_above_floor():
    out = bound_dna(OwlDNA(precision=0.32), OwlDNA(precision=0.0), OwlDNA(precision=0.5))
    assert out.precision >= 0.3  # standard TRAIT_FLOOR applies


def test_neutral_anchor_preserves_legacy_envelope():
    # anchor=neutral 0.5 → band [0.2,0.8] = old behavior
    out = bound_dna(OwlDNA(curiosity=0.79), OwlDNA(curiosity=0.99), OwlDNA())
    assert abs(out.curiosity - 0.8) < 1e-9
