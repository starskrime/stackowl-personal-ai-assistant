"""Single source of truth for the DNA neutral value and the canonical trait order.
Kills the scattered 0.5 (8+ sites) and trait-list (6+ sites) duplication. Story C."""
from __future__ import annotations

NEUTRAL: float = 0.5
TRAIT_NAMES: tuple[str, ...] = (
    "challenge_level", "verbosity", "curiosity", "formality", "creativity", "precision",
)
