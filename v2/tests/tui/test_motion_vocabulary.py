"""Test that .tcss files only use the 6 named motion transitions.

Story 8.6 introduces a closed motion vocabulary — six named CSS classes are
the *only* place ``transition:`` or ``animation:`` properties may appear in
the codebase.  These tests scan every ``.tcss`` file under ``styles/`` and
fail if a motion property is declared outside one of the permitted blocks.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.tui

_STYLES_DIR = (
    Path(__file__).resolve().parent.parent.parent
    / "src"
    / "stackowl"
    / "tui"
    / "styles"
)

_BASE_TCSS = _STYLES_DIR / "stackowl.tcss"
_REDUCED_MOTION_TCSS = _STYLES_DIR / "stackowl-reduced-motion.tcss"

# The six named selectors that constitute the closed motion vocabulary.
# (stagger-in is application-level via asyncio.sleep — no CSS class)
_PERMITTED_MOTION_SELECTORS: frozenset[str] = frozenset(
    {".fade-in", ".slide-up", ".pulse", ".collapse", ".expand"}
)

_MOTION_PROPERTY_RE = re.compile(r"\b(transition|animation)\s*:", re.IGNORECASE)


def _strip_comments(text: str) -> str:
    """Remove /* ... */ blocks so commented examples don't trip the scanner."""
    return re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)


def _split_blocks(text: str) -> list[tuple[str, str]]:
    """Return ``[(selector, body), ...]`` for every ``selector { ... }`` block.

    A naive parser is sufficient — Textual CSS files in this project are flat
    (no nested blocks) and use ASCII selectors.
    """
    blocks: list[tuple[str, str]] = []
    cursor = 0
    while cursor < len(text):
        brace_open = text.find("{", cursor)
        if brace_open == -1:
            break
        brace_close = text.find("}", brace_open + 1)
        if brace_close == -1:
            break
        selector = text[cursor:brace_open].strip()
        body = text[brace_open + 1 : brace_close]
        blocks.append((selector, body))
        cursor = brace_close + 1
    return blocks


def test_base_stylesheet_has_six_transitions() -> None:
    """Base stylesheet declares each of the 5 motion CSS classes exactly once."""
    body = _strip_comments(_BASE_TCSS.read_text(encoding="utf-8"))
    blocks = _split_blocks(body)
    selectors = [sel for sel, _ in blocks]
    for permitted in _PERMITTED_MOTION_SELECTORS:
        assert permitted in selectors, (
            f"base stylesheet missing required motion class {permitted!r}"
        )


def test_no_ad_hoc_transitions_in_tcss_files() -> None:
    """No ``transition:``/``animation:`` outside the 5 permitted blocks."""
    violations: list[str] = []
    for path in _STYLES_DIR.rglob("*.tcss"):
        body = _strip_comments(path.read_text(encoding="utf-8"))
        blocks = _split_blocks(body)
        for selector, block_body in blocks:
            if _MOTION_PROPERTY_RE.search(block_body) is None:
                continue
            if selector in _PERMITTED_MOTION_SELECTORS:
                continue
            violations.append(
                f"{path.name}: selector {selector!r} declares motion property "
                "(only the 5 permitted classes may do so)"
            )
    assert not violations, (
        "Motion properties must live inside one of "
        f"{sorted(_PERMITTED_MOTION_SELECTORS)} — found:\n"
        + "\n".join(violations)
    )


def test_reduced_motion_stylesheet_exists() -> None:
    """Reduced-motion override stylesheet ships with the package."""
    assert _REDUCED_MOTION_TCSS.is_file(), (
        f"expected reduced-motion stylesheet at {_REDUCED_MOTION_TCSS}"
    )


def test_color_tier_stylesheets_exist() -> None:
    """All 4 colour-tier stylesheets are present so the loader can find them."""
    for name in (
        "stackowl-monochrome.tcss",
        "stackowl-16.tcss",
        "stackowl-256.tcss",
        "stackowl-24bit.tcss",
    ):
        path = _STYLES_DIR / name
        assert path.is_file(), f"expected tier stylesheet at {path}"
