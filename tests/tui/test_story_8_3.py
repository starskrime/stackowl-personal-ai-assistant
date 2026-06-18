"""Story 8.3 — ConstellationView, layout tiers, i18n, messages, TCSS purity.

Compose-area behaviour and autocomplete helpers live in
``test_story_8_3_compose.py`` to keep each file under the 300-line limit.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from stackowl.events.bus import EventBus
from stackowl.tui.coordinator import UIStateCoordinator
from stackowl.tui.i18n import clear_translations, localize, register_translations
from stackowl.tui.layout import LayoutTier, compute_tier
from stackowl.tui.messages import (
    AutocompleteSelectedMessage,
    ComposeAreaStateMessage,
    ComposeSubmittedMessage,
    LayoutTierChangedMessage,
)
from stackowl.tui.widgets.constellation_view import ConstellationView, OwlCard

pytestmark = pytest.mark.tui


_WIDGETS_DIR = (
    Path(__file__).resolve().parent.parent.parent
    / "src"
    / "stackowl"
    / "tui"
    / "widgets"
)
_CONSTELLATION_TCSS = _WIDGETS_DIR / "constellation_view.tcss"
_COMPOSE_TCSS = _WIDGETS_DIR / "compose_area.tcss"


class _FakeApp:
    """Minimal Textual.App stand-in capturing posted messages."""

    def __init__(self) -> None:
        self.posted: list[Any] = []

    def call_from_thread(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        return fn(*args, **kwargs)

    def post_message(self, message: Any) -> None:
        self.posted.append(message)


# ---------------------------------------------------------------------------
# A. LayoutTier / compute_tier
# ---------------------------------------------------------------------------


def test_compute_tier_minimal_when_cols_below_60() -> None:
    assert compute_tier(20) == LayoutTier.MINIMAL
    assert compute_tier(59) == LayoutTier.MINIMAL


def test_compute_tier_compact_at_80() -> None:
    assert compute_tier(80) == LayoutTier.COMPACT


def test_compute_tier_standard_at_120() -> None:
    assert compute_tier(120) == LayoutTier.STANDARD


def test_compute_tier_expanded_at_200() -> None:
    assert compute_tier(200) == LayoutTier.EXPANDED


def test_compute_tier_boundary_exactly_60_is_compact() -> None:
    assert compute_tier(60) == LayoutTier.COMPACT


def test_compute_tier_boundary_exactly_100_is_standard() -> None:
    assert compute_tier(100) == LayoutTier.STANDARD


def test_compute_tier_boundary_exactly_160_is_expanded() -> None:
    assert compute_tier(160) == LayoutTier.EXPANDED


# ---------------------------------------------------------------------------
# B. ConstellationView / OwlCard
# ---------------------------------------------------------------------------


def test_constellation_view_default_css_uses_tokens() -> None:
    css = ConstellationView.DEFAULT_CSS
    assert "$color-bg-elevated" in css
    assert "$color-border" in css


def test_owl_card_render_collapsed_uses_2char_avatar() -> None:
    card = OwlCard(owl_name="secretary", is_secretary=True)
    out = card.render_collapsed()
    assert out[:2] == "SE"
    assert " " in out


def test_owl_card_render_collapsed_short_name_is_padded() -> None:
    card = OwlCard(owl_name="a", is_secretary=False)
    out = card.render_collapsed()
    # 2-char avatar must remain 2 chars wide even for 1-char owl names.
    assert out[:2] == "A "


def test_owl_card_render_full_includes_owl_name() -> None:
    card = OwlCard(owl_name="parrot", is_secretary=False)
    out = card.render_full()
    assert "parrot" in out
    assert "tier=" in out


def test_constellation_view_set_registry_builds_cards() -> None:
    from stackowl.owls.registry import OwlRegistry

    view = ConstellationView()
    registry = OwlRegistry.with_default_secretary()
    view.set_registry(registry)
    cards = view.cards()
    assert len(cards) == 1
    assert cards[0].owl_name == "secretary"
    assert cards[0].is_secretary is True


# ---------------------------------------------------------------------------
# C. i18n stub
# ---------------------------------------------------------------------------


def test_localize_returns_key_when_no_translation_registered() -> None:
    clear_translations()
    assert localize("any.key.that.doesnt.exist") == "any.key.that.doesnt.exist"


def test_localize_returns_translation_when_registered() -> None:
    clear_translations()
    register_translations("en", {"compose.placeholder": "Type a message…"})
    assert localize("compose.placeholder", lang="en") == "Type a message…"
    # Auto fall-back to en when lang unknown.
    assert localize("compose.placeholder", lang="zz") == "Type a message…"
    clear_translations()


# ---------------------------------------------------------------------------
# D. Message types — frozen + isinstance
# ---------------------------------------------------------------------------


def test_layout_tier_changed_message_is_frozen_message_type() -> None:
    from stackowl.tui.messages._base import FrozenMessage

    msg = LayoutTierChangedMessage(tier="standard")
    assert isinstance(msg, FrozenMessage)
    assert msg.tier == "standard"


def test_compose_submitted_message_is_frozen_message_type() -> None:
    from stackowl.tui.messages._base import FrozenMessage

    msg = ComposeSubmittedMessage(text="hi")
    assert isinstance(msg, FrozenMessage)
    assert msg.text == "hi"


def test_autocomplete_selected_message_carries_completion_type() -> None:
    msg = AutocompleteSelectedMessage(selected="help", completion_type="command")
    assert msg.selected == "help"
    assert msg.completion_type == "command"


# ---------------------------------------------------------------------------
# E. TCSS purity — no hex / rgb literals
# ---------------------------------------------------------------------------


def test_constellation_view_tcss_exists() -> None:
    assert _CONSTELLATION_TCSS.is_file()


def test_compose_area_tcss_exists() -> None:
    assert _COMPOSE_TCSS.is_file()


def test_constellation_view_tcss_uses_tokens_only() -> None:
    body = _CONSTELLATION_TCSS.read_text(encoding="utf-8")
    stripped = re.sub(r"/\*.*?\*/", "", body, flags=re.DOTALL)
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", stripped)
    assert not re.search(r"rgba?\s*\(", stripped)
    assert "$color-bg-elevated" in stripped
    assert "$color-border" in stripped


def test_compose_area_tcss_uses_tokens_only() -> None:
    body = _COMPOSE_TCSS.read_text(encoding="utf-8")
    stripped = re.sub(r"/\*.*?\*/", "", body, flags=re.DOTALL)
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", stripped)
    assert not re.search(r"rgba?\s*\(", stripped)
    assert "$color-surface" in stripped
    assert "$color-border" in stripped


# ---------------------------------------------------------------------------
# F. Coordinator — MCP spectator event mappings
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_coordinator_dispatches_mcp_spectator_active_to_compose_state() -> None:
    bus = EventBus()
    app = _FakeApp()
    coord = UIStateCoordinator(app=app, event_bus=bus)  # type: ignore[arg-type]
    await coord._dispatch("mcp_spectator_active", {})
    assert len(app.posted) == 1
    msg = app.posted[0]
    assert isinstance(msg, ComposeAreaStateMessage)
    assert msg.state == "mcp-disabled"


@pytest.mark.asyncio
async def test_coordinator_dispatches_mcp_spectator_disconnected_to_idle() -> None:
    bus = EventBus()
    app = _FakeApp()
    coord = UIStateCoordinator(app=app, event_bus=bus)  # type: ignore[arg-type]
    await coord._dispatch("mcp_spectator_disconnected", {})
    assert len(app.posted) == 1
    msg = app.posted[0]
    assert isinstance(msg, ComposeAreaStateMessage)
    assert msg.state == "idle"
