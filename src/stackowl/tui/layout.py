"""Layout tier — terminal-width buckets that drive widget adaptation.

Every TUI widget chooses its rendering based on the current
:class:`LayoutTier`.  Tiers are derived from the terminal column count so the
layout reacts uniformly to ``Resize`` events.
"""

from __future__ import annotations

from enum import Enum


class LayoutTier(Enum):
    """Discrete terminal-width buckets that drive responsive layout choices."""

    MINIMAL = "minimal"    # < 60 cols  — hide non-essential panes
    COMPACT = "compact"    # 60-99      — collapse to icon strips
    STANDARD = "standard"  # 100-159    — full default layout
    EXPANDED = "expanded"  # >= 160     — extra side panes


def compute_tier(cols: int) -> LayoutTier:
    """Bucket a terminal column count into the matching :class:`LayoutTier`.

    Args:
        cols: Current terminal width in columns.

    Returns:
        The :class:`LayoutTier` to which ``cols`` belongs.  Boundary columns
        round up — exactly 60 cols selects ``COMPACT``, exactly 100 selects
        ``STANDARD``, exactly 160 selects ``EXPANDED``.
    """
    if cols < 60:
        return LayoutTier.MINIMAL
    if cols < 100:
        return LayoutTier.COMPACT
    if cols < 160:
        return LayoutTier.STANDARD
    return LayoutTier.EXPANDED
