"""Helpers for :class:`ConstellationView` — pure formatting logic.

Keeping these out of the widget keeps the widget itself focused on Textual
plumbing and lets the formatting be unit-tested without spinning up an app.
"""

from __future__ import annotations

from dataclasses import dataclass

from stackowl.tui.glyphs import GLYPH_DNA_BARS, GLYPH_STEP_COMPLETE

_AVATAR_WIDTH = 2
_NEUTRAL_TRAIT = 0.5


@dataclass(frozen=True)
class OwlCardModel:
    """Render-side data for a single :class:`OwlCard`.

    Plain immutable record — built fresh on every refresh from the registry
    so the widget never holds onto stale manifest references.
    """

    owl_name: str
    is_secretary: bool
    tier: str
    dominant_trait_name: str
    dominant_trait_value: float
    last_active: str


def make_avatar(owl_name: str) -> str:
    """Return the 2-char avatar shown in the collapsed icon strip."""
    cleaned = owl_name.strip() or "??"
    return cleaned[:_AVATAR_WIDTH].upper().ljust(_AVATAR_WIDTH)


def render_collapsed(owl_name: str) -> str:
    """Render the icon-strip row used by ``COMPACT`` layouts."""
    return f"{make_avatar(owl_name)} {GLYPH_STEP_COMPLETE}"


def dna_bar(value: float) -> str:
    """Return a single block-glyph representing ``value`` in ``[0, 1]``."""
    clamped = max(0.0, min(1.0, value))
    last_index = len(GLYPH_DNA_BARS) - 1
    idx = int(round(clamped * last_index))
    return str(GLYPH_DNA_BARS[idx])


def render_full(model: OwlCardModel) -> str:
    """Render the full card layout used by ``STANDARD`` and ``EXPANDED``."""
    avatar = make_avatar(model.owl_name)
    secretary_marker = "*" if model.is_secretary else " "
    bar = dna_bar(model.dominant_trait_value)
    trait = model.dominant_trait_name or "neutral"
    return (
        f"{avatar}{secretary_marker} {model.owl_name}\n"
        f" tier={model.tier} {bar} {trait}\n"
        f" last={model.last_active}"
    )


def pick_dominant_trait(traits: dict[str, float]) -> tuple[str, float]:
    """Pick the trait that deviates furthest from neutral (0.5)."""
    if not traits:
        return ("neutral", _NEUTRAL_TRAIT)
    name, value = max(traits.items(), key=lambda kv: abs(kv[1] - _NEUTRAL_TRAIT))
    return (name, value)
