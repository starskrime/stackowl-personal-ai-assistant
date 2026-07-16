"""bound_dna — the single DNA-safety governor for evolution (rate+range+floor).

OwlDNA already enforces [0,1] on construction (Field ge/le); this adds the
per-batch max-delta, the neutral envelope, and the judgment-trait floors that
the model does NOT enforce.
"""
from __future__ import annotations

from enum import StrEnum

from stackowl.owls.dna import _MUTABLE_TRAITS, OwlDNA
from stackowl.owls.evolution_limits import (
    ENVELOPE,
    FLOOR_TRAITS,
    MAX_DELTA,
    TRAIT_FLOOR,
)


class SignalStrength(StrEnum):
    """How trustworthy the evidence behind a proposed DNA delta is (FR-6).

    Defined once here and imported by every propose-stage caller — not
    re-derived per caller (architecture spine, Consistency Conventions).
    """

    VERIFIED = "verified"              # attribution over scored, eligible outcomes
    OUTCOME_BINARY = "outcome_binary"  # bare success/fail — no current producer (Story 2.4)
    LLM_QUALITY = "llm_quality"        # a single LLM completion's opinion, no TaskOutcome backing


# Operator-tunable (no existing precedent to match) — VERIFIED=1.0 keeps
# today's attribution-path magnitude byte-identical (NFR-5); the other tiers
# narrow the effective delta, never widen it (AD-4).
_SIGNAL_STRENGTH_MULTIPLIER: dict[SignalStrength, float] = {
    SignalStrength.VERIFIED: 1.0,
    SignalStrength.OUTCOME_BINARY: 0.6,
    SignalStrength.LLM_QUALITY: 0.3,
}


def scale_by_signal_strength(delta: float, signal: SignalStrength) -> float:
    """Scale a raw per-trait delta by how strong the signal behind it is."""
    return delta * _SIGNAL_STRENGTH_MULTIPLIER[signal]


def bound_dna(
    current: OwlDNA,
    proposed: OwlDNA,
    anchor: OwlDNA,
    signal: SignalStrength = SignalStrength.VERIFIED,
) -> OwlDNA:
    """Rate-cap + clamp into the per-owl envelope [anchor±ENVELOPE] + author-deferring floor.

    ``signal`` (default VERIFIED, preserving exact pre-Story-2.4 behavior for
    any caller that doesn't pass it — NFR-5) scales the raw per-trait delta
    DOWN before the existing MAX_DELTA/ENVELOPE/TRAIT_FLOOR clamps run — those
    clamps are never parameterized by signal strength; they remain the final,
    unconditional ceiling (AD-4, FR-7).
    """
    updates: dict[str, float] = {}
    for trait in _MUTABLE_TRAITS:
        cur = float(getattr(current, trait))
        prop = float(getattr(proposed, trait))
        anc = float(getattr(anchor, trait))
        effective_delta = scale_by_signal_strength(prop - cur, signal)
        delta = max(-MAX_DELTA, min(MAX_DELTA, effective_delta))
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
