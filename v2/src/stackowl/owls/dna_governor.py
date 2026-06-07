"""bound_dna — the single DNA-safety governor for evolution (rate+range+floor).

OwlDNA already enforces [0,1] on construction (Field ge/le); this adds the
per-batch max-delta, the neutral envelope, and the judgment-trait floors that
the model does NOT enforce.
"""
from __future__ import annotations

from stackowl.owls.dna import _MUTABLE_TRAITS, OwlDNA
from stackowl.owls.evolution_limits import (
    ENVELOPE,
    FLOOR_TRAITS,
    MAX_DELTA,
    TRAIT_FLOOR,
)


def bound_dna(current: OwlDNA, proposed: OwlDNA, anchor: OwlDNA) -> OwlDNA:
    """Rate-cap + clamp into the per-owl envelope [anchor±ENVELOPE] + author-deferring floor."""
    updates: dict[str, float] = {}
    for trait in _MUTABLE_TRAITS:
        cur = float(getattr(current, trait))
        prop = float(getattr(proposed, trait))
        anc = float(getattr(anchor, trait))
        delta = max(-MAX_DELTA, min(MAX_DELTA, prop - cur))
        lo, hi = max(0.0, anc - ENVELOPE), min(1.0, anc + ENVELOPE)
        moved = max(lo, min(hi, cur + delta))
        if trait in FLOOR_TRAITS:
            moved = max(min(TRAIT_FLOOR, anc), moved)
        updates[trait] = moved
    return current.model_copy(update=updates)
