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
            # AUTHOR-DEFERRING FLOOR (F085) — INTENTIONAL, not a hard
            # ``max(TRAIT_FLOOR, moved)``. The effective floor is
            # ``min(TRAIT_FLOOR, anchor)``:
            #   * anchor >= TRAIT_FLOOR (the common case): a real 0.3 floor.
            #     This is LOAD-BEARING — the envelope low bound (anchor-ENVELOPE)
            #     can dip below 0.3, so without it evolution could erode a
            #     judgment trait beneath the floor.
            #   * anchor < TRAIT_FLOOR: the author deliberately created a
            #     low-challenge/low-precision persona (e.g. a deferential
            #     assistant). We DEFER to that authored anchor rather than force
            #     it up to 0.3 — consistent with the authored-anchor envelope
            #     design (the governor protects the author's intent, it does not
            #     override it). Evolution still cannot push BELOW the author's
            #     value; it may only sit at it.
            # A hard floor here would override a deliberately low-authored
            # judgment trait — do NOT "simplify" this to max(TRAIT_FLOOR, moved).
            moved = max(min(TRAIT_FLOOR, anc), moved)
        updates[trait] = moved
    return current.model_copy(update=updates)
