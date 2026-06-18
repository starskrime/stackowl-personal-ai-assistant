"""Safety limits for DNA evolution (mirrors owls/delegation_limits.py).

The DNA-evolution feedback loop is a positive-feedback control system; these
bound it so conversation-driven evolution cannot slow-poison the persona.
"""
from stackowl.owls.dna_defaults import NEUTRAL

DNA_NEUTRAL = NEUTRAL        # every authored owl DNA defaults to neutral 0.5
MAX_DELTA = 0.05             # max move per trait per evolution batch (rate cap)
# Evolution orbits DNA_NEUTRAL +/- ENVELOPE (range cap). 0.3 → band [0.2, 0.8],
# strictly WIDER than the injector's 0.3-0.7 deadband so a sustained trend CAN
# cross a directive threshold (>0.7 / <0.3) — i.e. evolution is perceptible — but
# can never roam to an extreme.
ENVELOPE = 0.3
# Judgment traits may never drop below this — the floor (load-bearing: it sits
# ABOVE the envelope's 0.2 low bound) keeps challenge_level/precision out of the
# low band so evolution can never disarm the owl's pushback / rigor.
TRAIT_FLOOR = 0.3
FLOOR_TRAITS = frozenset({"challenge_level", "precision"})  # willingness to push back / be precise
