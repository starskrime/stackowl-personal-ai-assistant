"""Verify all design-token contrast pairs satisfy WCAG AA luminance ratios."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.tui

_CONTRAST_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "src"
    / "stackowl"
    / "tui"
    / "styles"
    / "contrast_pairs.yaml"
)


def _relative_luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))

    def channel(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def _contrast_ratio(fg: str, bg: str) -> float:
    l1 = _relative_luminance(fg)
    l2 = _relative_luminance(bg)
    lighter, darker = max(l1, l2), min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


def test_relative_luminance_known_values() -> None:
    # White luminance = 1.0, black = 0.0
    assert _relative_luminance("#ffffff") == pytest.approx(1.0, abs=1e-6)
    assert _relative_luminance("#000000") == pytest.approx(0.0, abs=1e-6)


def test_contrast_ratio_white_on_black() -> None:
    assert _contrast_ratio("#ffffff", "#000000") == pytest.approx(21.0, abs=1e-3)


def test_all_design_pairs_meet_wcag_aa() -> None:
    raw = yaml.safe_load(_CONTRAST_PATH.read_text(encoding="utf-8"))
    pairs = raw.get("pairs", [])
    assert pairs, "contrast_pairs.yaml must define at least one pair"
    failures: list[str] = []
    for pair in pairs:
        name = pair["name"]
        fg = pair["foreground"]
        bg = pair["background"]
        text_type = pair.get("text_type", "normal")
        ratio = _contrast_ratio(fg, bg)
        required = 3.0 if text_type == "large" else 4.5
        if ratio < required:
            failures.append(
                f"{name}: {fg} on {bg} ratio={ratio:.2f} < required {required:.1f}"
            )
    assert not failures, "\n".join(failures)
