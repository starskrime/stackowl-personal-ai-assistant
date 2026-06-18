"""Story 8.4 (part B) — ParliamentPanel widget, coordinator wiring, TCSS, onboarding."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from stackowl.events.bus import EventBus
from stackowl.tui.coordinator import UIStateCoordinator
from stackowl.tui.messages import (
    ParliamentClosedMessage,
    ParliamentRoundMessage,
    ParliamentRoundStartedMessage,
    ParliamentStartedMessage,
    SynthesisArrivedMessage,
)
from stackowl.tui.widgets.owl_round_panel import OwlRoundPanel
from stackowl.tui.widgets.parliament_panel import ParliamentPanel
from stackowl.tui.widgets.parliament_panel_helpers import OnboardingStore

pytestmark = pytest.mark.tui

_WIDGETS_DIR = (
    Path(__file__).resolve().parent.parent.parent
    / "src" / "stackowl" / "tui" / "widgets"
)
_PARLIAMENT_TCSS = _WIDGETS_DIR / "parliament_panel.tcss"
_MIGRATIONS_DIR = (
    Path(__file__).resolve().parent.parent.parent
    / "src" / "stackowl" / "db" / "migrations"
)


class _FakeApp:
    """Minimal Textual.App stand-in capturing posted messages."""

    def __init__(self) -> None:
        self.posted: list[Any] = []

    def call_from_thread(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        return fn(*args, **kwargs)

    def post_message(self, message: Any) -> None:
        self.posted.append(message)


# A. ParliamentPanel state mutations ---------------------------------------


def test_parliament_panel_default_css_uses_design_tokens() -> None:
    css = ParliamentPanel.DEFAULT_CSS
    assert "$color-bg-elevated" in css
    assert "$color-parliament" in css
    assert "layer: overlay" in css


def test_parliament_panel_on_started_sets_session_id_and_round_panels() -> None:
    panel = ParliamentPanel()
    panel.on_parliament_started_message(
        ParliamentStartedMessage(
            session_id="s99", owl_names=("Alice", "Bob"), trigger="explicit"
        )
    )
    assert panel.session_id == "s99"
    assert panel.current_round == 1
    assert set(panel.round_panels.keys()) == {"Alice", "Bob"}


def test_parliament_panel_on_started_sets_display_true() -> None:
    panel = ParliamentPanel()
    panel.display = False
    panel.on_parliament_started_message(
        ParliamentStartedMessage(session_id="s", owl_names=("o1",))
    )
    assert panel.display is True


def test_parliament_panel_on_closed_sets_display_false() -> None:
    panel = ParliamentPanel()
    panel.display = True
    panel.on_parliament_closed_message(ParliamentClosedMessage(session_id="s"))
    assert panel.display is False


def test_parliament_panel_round_started_updates_current_round() -> None:
    panel = ParliamentPanel()
    panel.on_parliament_started_message(
        ParliamentStartedMessage(session_id="s", owl_names=("o1",))
    )
    panel.on_parliament_round_started_message(
        ParliamentRoundStartedMessage(session_id="s", round_number=3)
    )
    assert panel.current_round == 3


def test_parliament_panel_round_started_collapses_prior_panels_after_round_1() -> None:
    panel = ParliamentPanel()
    panel.on_parliament_started_message(
        ParliamentStartedMessage(session_id="s", owl_names=("o1", "o2"))
    )
    panel.on_parliament_round_started_message(
        ParliamentRoundStartedMessage(session_id="s", round_number=2)
    )
    for sub_panel in panel.round_panels.values():
        assert sub_panel.collapsed is True


def test_parliament_panel_round_message_registers_new_owls() -> None:
    panel = ParliamentPanel()
    panel.on_parliament_started_message(
        ParliamentStartedMessage(session_id="s", owl_names=("o1",))
    )
    panel.on_parliament_round_message(
        ParliamentRoundMessage(
            session_id="s", round_number=1, owl_responses={"new_owl": "hi"}
        )
    )
    assert "new_owl" in panel.round_panels


def test_parliament_panel_synthesis_handler_does_not_raise_without_mount() -> None:
    panel = ParliamentPanel()
    panel.on_synthesis_arrived_message(
        SynthesisArrivedMessage(
            session_id="s", consensus="agree", recommendation="do Y",
            confidence=0.5, disagreements=("d1",),
        )
    )


# B. OwlRoundPanel transitions ----------------------------------------------


def test_owl_round_panel_starts_uncollapsed() -> None:
    panel = OwlRoundPanel("alice")
    assert panel.owl_name == "alice"
    assert panel.collapsed is False


def test_owl_round_panel_collapse_then_uncollapse() -> None:
    panel = OwlRoundPanel("bob")
    panel.collapse()
    assert panel.collapsed is True
    panel.uncollapse()
    assert panel.collapsed is False


def test_owl_round_panel_collapse_threshold_is_two_lines() -> None:
    assert OwlRoundPanel("c").collapse_threshold == 2


def test_owl_round_panel_append_text_does_not_raise_without_mount() -> None:
    panel = OwlRoundPanel("c")
    panel.append_text("hello")
    panel.append_text("world")


# C. Coordinator wiring -----------------------------------------------------


@pytest.mark.asyncio
async def test_coordinator_dispatches_parliament_started() -> None:
    app = _FakeApp()
    coord = UIStateCoordinator(app=app, event_bus=EventBus())  # type: ignore[arg-type]
    await coord._dispatch(
        "parliament_started",
        {"session_id": "abc", "owl_names": ["o1", "o2"], "trigger": "multi_mention"},
    )
    assert len(app.posted) == 1
    msg = app.posted[0]
    assert isinstance(msg, ParliamentStartedMessage)
    assert msg.session_id == "abc"
    assert msg.owl_names == ("o1", "o2")
    assert msg.trigger == "multi_mention"


@pytest.mark.asyncio
async def test_coordinator_dispatches_parliament_round_started() -> None:
    app = _FakeApp()
    coord = UIStateCoordinator(app=app, event_bus=EventBus())  # type: ignore[arg-type]
    await coord._dispatch(
        "parliament_round_started", {"session_id": "abc", "round_number": 2}
    )
    msg = app.posted[0]
    assert isinstance(msg, ParliamentRoundStartedMessage)
    assert msg.round_number == 2


@pytest.mark.asyncio
async def test_coordinator_dispatches_synthesis_arrived() -> None:
    app = _FakeApp()
    coord = UIStateCoordinator(app=app, event_bus=EventBus())  # type: ignore[arg-type]
    await coord._dispatch(
        "synthesis_arrived",
        {
            "session_id": "abc", "consensus": "agree", "recommendation": "do",
            "confidence": 0.42, "disagreements": ["d1"],
        },
    )
    msg = app.posted[0]
    assert isinstance(msg, SynthesisArrivedMessage)
    assert msg.confidence == pytest.approx(0.42)
    assert msg.disagreements == ("d1",)


@pytest.mark.asyncio
async def test_coordinator_dispatches_parliament_session_closed() -> None:
    app = _FakeApp()
    coord = UIStateCoordinator(app=app, event_bus=EventBus())  # type: ignore[arg-type]
    await coord._dispatch("parliament_session_closed", {"session_id": "abc"})
    msg = app.posted[0]
    assert isinstance(msg, ParliamentClosedMessage)
    assert msg.session_id == "abc"


# D. TCSS purity + migration presence --------------------------------------


def test_parliament_panel_tcss_exists() -> None:
    assert _PARLIAMENT_TCSS.is_file()


def test_parliament_panel_tcss_uses_tokens_only() -> None:
    body = _PARLIAMENT_TCSS.read_text(encoding="utf-8")
    stripped = re.sub(r"/\*.*?\*/", "", body, flags=re.DOTALL)
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", stripped)
    assert not re.search(r"rgba?\s*\(", stripped)
    assert "$color-bg-elevated" in stripped
    assert "$color-parliament" in stripped


def test_migration_0021_onboarding_exists() -> None:
    path = _MIGRATIONS_DIR / "0021_onboarding.sql"
    assert path.is_file()
    body = path.read_text(encoding="utf-8")
    assert "CREATE TABLE" in body
    assert "onboarding" in body


def test_migration_count_is_21(tmp_path: Path) -> None:
    # Name kept historical for log searchability. Asserts the runner applies
    # exactly the migration .sql files present on disk; the expected count is
    # derived dynamically from the actual .sql files (no more manual bumps on
    # every new migration).
    from stackowl.db.migrations.runner import MigrationRunner

    expected = len(sorted(_MIGRATIONS_DIR.glob("*.sql")))
    results = MigrationRunner(db_path=tmp_path / "count.db").run()
    assert len(results) == expected


# E. Onboarding store + check_onboarding -----------------------------------


def _bootstrap_onboarding_table(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS onboarding ("
            " key TEXT NOT NULL PRIMARY KEY,"
            " shown_at TEXT NOT NULL DEFAULT"
            " (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')))"
        )
        conn.commit()


def test_onboarding_store_records_and_detects_shown(tmp_path: Path) -> None:
    db = tmp_path / "ob.db"
    _bootstrap_onboarding_table(db)
    store = OnboardingStore(db)
    assert store.was_shown("k1") is False
    store.mark_shown("k1")
    assert store.was_shown("k1") is True


def test_parliament_panel_check_onboarding_shows_only_once(tmp_path: Path) -> None:
    db = tmp_path / "ob.db"
    _bootstrap_onboarding_table(db)
    panel = ParliamentPanel(onboarding_store=OnboardingStore(db))
    assert panel.check_onboarding() is True
    assert panel.check_onboarding() is False
    assert panel.onboarding_tip_shown is True


def test_parliament_panel_check_onboarding_without_store_returns_false() -> None:
    panel = ParliamentPanel()
    assert panel.check_onboarding() is False
    assert panel.onboarding_tip_shown is False
