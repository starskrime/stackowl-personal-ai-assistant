"""Story 1 — Banner widget: pinned wordmark, localized tagline, token-pure CSS."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from stackowl.tui.i18n import clear_translations, localize
from stackowl.tui.i18n_strings import install_default_translations
from stackowl.tui.widgets.banner import Banner

pytestmark = pytest.mark.tui

_HEADER_TS = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "cli"
    / "v2"
    / "io"
    / "header.ts"
)

_TCSS_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "src"
    / "stackowl"
    / "tui"
    / "styles"
    / "stackowl.tcss"
)

_HEX_RE = re.compile(r"#[0-9a-fA-F]{3,8}\b")
_RGB_RE = re.compile(r"rgba?\s*\(")
_LOGO_RE = re.compile(r'text:\s*"([^"]*)"')


def test_banner_has_six_art_lines() -> None:
    assert len(Banner.LOGO_LINES) == 6


def test_banner_art_matches_legacy_header() -> None:
    raw = _HEADER_TS.read_text(encoding="utf-8")
    legacy = _LOGO_RE.findall(raw)
    assert len(legacy) == 6, f"expected 6 logo lines in header.ts, got {len(legacy)}"
    ours = [line for line, _ in Banner.LOGO_LINES]
    assert ours == legacy


def test_banner_brightness_split() -> None:
    bright_flags = [bright for _, bright in Banner.LOGO_LINES]
    assert bright_flags[:3] == [True, True, True]
    assert bright_flags[3:] == [False, False, False]


def test_banner_css_docks_top_layer_top_height_9() -> None:
    css = Banner.DEFAULT_CSS
    assert "dock: top" in css
    assert "layer: top" in css
    assert "height: 9" in css


def test_banner_css_uses_only_tokens() -> None:
    css = Banner.DEFAULT_CSS
    assert not _HEX_RE.search(css), "Banner CSS must not contain hex literals"
    assert not _RGB_RE.search(css), "Banner CSS must not contain rgb(...) literals"


def test_banner_tagline_localized() -> None:
    clear_translations()
    install_default_translations()
    assert localize("banner.tagline_primary") == "Personal AI Assistant"
    assert localize("banner.tagline_secondary") == "Challenge Everything"


def test_stackowl_tcss_defines_banner_tokens() -> None:
    text = _TCSS_PATH.read_text(encoding="utf-8")
    for token in (
        "$color-banner-amber",
        "$color-banner-red",
        "$color-banner-rule",
    ):
        assert token in text, f"missing banner token {token!r} in stackowl.tcss"


@pytest.mark.asyncio
async def test_banner_mounts_in_full_app() -> None:
    """The 5-zone app must mount cleanly with the Banner pinned and rules filled.

    Guards the layer/dock interaction between Banner (dock:top, layer:top) and
    the pre-existing PipelineStrip (layer:top) — a regression here would surface
    as a mount-time crash or a missing banner.
    """
    from stackowl.events.bus import EventBus
    from stackowl.tui.app import StackOwlApp

    app = StackOwlApp(EventBus(), command_names=["help"], owl_names=["athena"])
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        banners = list(app.query(Banner))
        assert len(banners) == 1, "exactly one Banner must be mounted"
        banner = banners[0]
        # All 6 art lines + 2 rules + tagline mounted as child Statics.
        from textual.widgets import Static

        statics = list(banner.query(Static))
        assert len(statics) == 9, f"expected 9 child Statics, got {len(statics)}"
        # Rules were filled to the 100-col test width (non-empty run of the glyph).
        top_rule = banner.query_one("#banner-rule-top", Static)
        assert "─" in str(top_rule.render())
