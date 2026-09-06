"""Token purity: no literal colors outside the canonical stackowl.tcss declaration file."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.tui._tcss import has_rgb_literal, hex_literals

pytestmark = pytest.mark.tui

_STYLES_DIR = (
    Path(__file__).resolve().parent.parent.parent
    / "src"
    / "stackowl"
    / "tui"
    / "styles"
)

_CANONICAL = "stackowl.tcss"




def _iter_tcss(root: Path):
    yield from root.rglob("*.tcss")


def test_no_literal_colors_outside_canonical_tcss() -> None:
    violations: list[str] = []
    for path in _iter_tcss(_STYLES_DIR):
        if path.name == _CANONICAL:
            continue
        body = path.read_text(encoding="utf-8")
        for literal in hex_literals(body):
            violations.append(f"{path}: hex literal {literal!r}")
        if has_rgb_literal(body):
            violations.append(f"{path}: rgb(...) literal found")
    assert not violations, (
        "Use $color-* tokens instead of literal colors:\n" + "\n".join(violations)
    )


def test_canonical_tcss_defines_at_least_one_color() -> None:
    """Sanity check — the canonical file must actually define hex tokens."""
    canonical = _STYLES_DIR / _CANONICAL
    body = canonical.read_text(encoding="utf-8")
    assert hex_literals(body), "stackowl.tcss is expected to declare hex tokens"
