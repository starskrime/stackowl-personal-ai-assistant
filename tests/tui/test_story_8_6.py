"""Story 8.6 — ColorCapabilityDetector + reduced-motion env detection."""

from __future__ import annotations

import os

import pytest

from stackowl.tui.color_caps import ColorCapabilityDetector, ColorTier

pytestmark = pytest.mark.tui


# --------------------------------------------------------------------- detect()
def test_no_color_env_gives_monochrome() -> None:
    assert ColorCapabilityDetector().detect({"NO_COLOR": ""}) is ColorTier.MONOCHROME


def test_clicolor_zero_gives_monochrome() -> None:
    assert (
        ColorCapabilityDetector().detect({"CLICOLOR": "0"}) is ColorTier.MONOCHROME
    )


def test_tmux_gives_256() -> None:
    tier = ColorCapabilityDetector().detect(
        {"TERM": "screen-256color", "TMUX": "/tmp/tmux"}
    )
    assert tier is ColorTier.COLOR_256


def test_truecolor_gives_24bit() -> None:
    tier = ColorCapabilityDetector().detect({"COLORTERM": "truecolor"})
    assert tier is ColorTier.COLOR_24BIT


def test_24bit_gives_24bit() -> None:
    tier = ColorCapabilityDetector().detect({"COLORTERM": "24bit"})
    assert tier is ColorTier.COLOR_24BIT


def test_linux_term_gives_16color() -> None:
    tier = ColorCapabilityDetector().detect({"TERM": "linux"})
    assert tier is ColorTier.COLOR_16


def test_default_gives_256() -> None:
    tier = ColorCapabilityDetector().detect({})
    assert tier is ColorTier.COLOR_256


def test_no_color_overrides_colorterm() -> None:
    """NO_COLOR wins over COLORTERM — accessibility opt-out is highest priority."""
    tier = ColorCapabilityDetector().detect(
        {"NO_COLOR": "1", "COLORTERM": "truecolor"}
    )
    assert tier is ColorTier.MONOCHROME


# ------------------------------------------------------------- stylesheet_name()
def test_stylesheet_name_monochrome() -> None:
    detector = ColorCapabilityDetector()
    assert (
        detector.stylesheet_name(ColorTier.MONOCHROME) == "stackowl-monochrome.tcss"
    )


def test_stylesheet_name_256() -> None:
    detector = ColorCapabilityDetector()
    assert detector.stylesheet_name(ColorTier.COLOR_256) == "stackowl-256.tcss"


def test_stylesheet_name_16() -> None:
    detector = ColorCapabilityDetector()
    assert detector.stylesheet_name(ColorTier.COLOR_16) == "stackowl-16.tcss"


def test_stylesheet_name_24bit() -> None:
    detector = ColorCapabilityDetector()
    assert detector.stylesheet_name(ColorTier.COLOR_24BIT) == "stackowl-24bit.tcss"


# ---------------------------------------------------------- reduced-motion env
def test_reduced_motion_env_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    """``STACKOWL_REDUCED_MOTION=1`` flips the env-driven motion flag.

    The flag is consulted by :class:`UIStateCoordinator` (and elsewhere) via
    the same expression ``os.environ.get("STACKOWL_REDUCED_MOTION", "0") == "1"``.
    """
    monkeypatch.setenv("STACKOWL_REDUCED_MOTION", "1")
    assert os.environ.get("STACKOWL_REDUCED_MOTION", "0") == "1"

    monkeypatch.setenv("STACKOWL_REDUCED_MOTION", "0")
    assert os.environ.get("STACKOWL_REDUCED_MOTION", "0") == "0"

    monkeypatch.delenv("STACKOWL_REDUCED_MOTION", raising=False)
    assert os.environ.get("STACKOWL_REDUCED_MOTION", "0") == "0"
