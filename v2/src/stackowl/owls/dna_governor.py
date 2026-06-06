"""bound_dna — the single DNA-safety governor for evolution (rate+range+floor).

OwlDNA already enforces [0,1] on construction (Field ge/le); this adds the
per-batch max-delta, the neutral envelope, and the judgment-trait floors that
the model does NOT enforce.
"""
from __future__ import annotations

from stackowl.owls.dna import _MUTABLE_TRAITS, OwlDNA
from stackowl.owls.evolution_limits import (
    DNA_NEUTRAL,
    ENVELOPE,
    FLOOR_TRAITS,
    MAX_DELTA,
    TRAIT_FLOOR,
)


def bound_dna(current: OwlDNA, proposed: OwlDNA) -> OwlDNA:
    """Return a safe DNA: per mutable trait cap the move to +/-MAX_DELTA, clamp into
    DNA_NEUTRAL +/- ENVELOPE, and hold the floor on judgment traits."""
    updates: dict[str, float] = {}
    lo, hi = DNA_NEUTRAL - ENVELOPE, DNA_NEUTRAL + ENVELOPE
    for trait in _MUTABLE_TRAITS:
        cur = float(getattr(current, trait))
        prop = float(getattr(proposed, trait))
        delta = max(-MAX_DELTA, min(MAX_DELTA, prop - cur))   # rate cap
        moved = max(lo, min(hi, cur + delta))                  # envelope (range cap)
        if trait in FLOOR_TRAITS:
            moved = max(TRAIT_FLOOR, moved)                    # safety floor
        updates[trait] = moved
    return current.model_copy(update=updates)
