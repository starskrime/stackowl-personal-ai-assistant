"""Safety limits for DNA evolution (mirrors owls/delegation_limits.py).

The DNA-evolution feedback loop is a positive-feedback control system; these
bound it so conversation-driven evolution cannot slow-poison the persona.
"""
DNA_NEUTRAL = 0.5            # every authored owl DNA defaults to neutral 0.5
MAX_DELTA = 0.05             # max move per trait per evolution batch (rate cap)
ENVELOPE = 0.2               # evolution orbits DNA_NEUTRAL +/- ENVELOPE (range cap)
TRAIT_FLOOR = 0.25           # judgment traits may never drop below this
FLOOR_TRAITS = frozenset({"challenge_level", "precision"})  # willingness to push back / be precise
