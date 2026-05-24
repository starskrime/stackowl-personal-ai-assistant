"""Story 8.5 part B — Toast, Evolution widgets, TCSS purity, coordinator wiring.

Sibling of ``test_story_8_5.py`` — split to keep each file under the
300-line limit (B2).
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path
from typing import Any

import pytest

from stackowl.tui.messages import (
    OpenEvolutionInspectionMessage,
    OverlayClosedMessage,
    ToastRequestMessage,
)
from stackowl.tui.widgets.evolution_badge import EvolutionBadge
from stackowl.tui.widgets.evolution_inspection_panel import EvolutionInspectionPanel
from stackowl.tui.widgets.toast_notification import ToastNotification

pytestmark = pytest.mark.tui


_WIDGETS_DIR = (
    Path(__file__).resolve().parent.parent.parent
    / "src" / "stackowl" / "tui" / "widgets"
)
_MEMORY_REVIEW_TCSS = _WIDGETS_DIR / "memory_review_panel.tcss"
_TOAST_TCSS = _WIDGETS_DIR / "toast_notification.tcss"
_EVOLUTION_INSPECTION_TCSS = _WIDGETS_DIR / "evolution_inspection_panel.tcss"


def _set_timer_recorder(
    monkeypatch: pytest.MonkeyPatch, widget: Any
) -> list[tuple[float, Any]]:
    calls: list[tuple[float, Any]] = []

    def _record(delay: float, callback: Any) -> Any:
        calls.append((delay, callback))
        return object()

    monkeypatch.setattr(widget, "set_timer", _record, raising=False)
    return calls


# ---------------------------------------------------------------------------
# ToastNotification
# ---------------------------------------------------------------------------


def test_toast_critical_has_no_auto_dismiss_seconds() -> None:
    toast = ToastNotification("boom", "critical")
    assert toast.auto_dismiss_seconds is None


def test_toast_low_auto_dismisses_at_three_seconds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    toast = ToastNotification("hint", "low")
    assert toast.auto_dismiss_seconds == pytest.approx(3.0)
    calls = _set_timer_recorder(monkeypatch, toast)
    toast.on_mount()
    assert calls and calls[0][0] == pytest.approx(3.0)


def test_toast_normal_auto_dismisses_at_six_seconds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    toast = ToastNotification("notice", "normal")
    assert toast.auto_dismiss_seconds == pytest.approx(6.0)
    calls = _set_timer_recorder(monkeypatch, toast)
    toast.on_mount()
    assert calls and calls[0][0] == pytest.approx(6.0)


def test_toast_critical_on_mount_schedules_no_timer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    toast = ToastNotification("boom", "critical")
    calls = _set_timer_recorder(monkeypatch, toast)
    toast.on_mount()
    assert calls == []


def test_toast_has_dismiss_bindings() -> None:
    toast = ToastNotification("hi", "normal")
    keys = [b.key for b in toast.BINDINGS]
    assert "d" in keys
    assert "enter" in keys


# ---------------------------------------------------------------------------
# EvolutionBadge / EvolutionInspectionPanel
# ---------------------------------------------------------------------------


def test_evolution_badge_on_mount_schedules_remove_after_pulse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    badge = EvolutionBadge("Athena", {"verbosity": (0.5, 0.7)})
    calls = _set_timer_recorder(monkeypatch, badge)
    badge.on_mount()
    assert calls
    assert calls[0][0] == pytest.approx(0.9)


def test_evolution_inspection_panel_overlay_name() -> None:
    assert EvolutionInspectionPanel.overlay_name == "evolution_inspection"


def test_evolution_inspection_load_records_owl_and_traits() -> None:
    panel = EvolutionInspectionPanel()
    panel.load("Athena", {"verbosity": (0.5, 0.7), "challenge": (0.4, 0.3)})
    assert panel.owl_name == "Athena"
    assert "verbosity" in panel.changed_traits
    assert "challenge" in panel.changed_traits


# ---------------------------------------------------------------------------
# TCSS purity checks
# ---------------------------------------------------------------------------


_HEX_RE = re.compile(r"#[0-9a-fA-F]{3,8}\b")
_RGB_RE = re.compile(r"rgba?\s*\(")


def _strip_comments(text: str) -> str:
    return re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)


@pytest.mark.parametrize(
    "tcss_path",
    [_MEMORY_REVIEW_TCSS, _TOAST_TCSS, _EVOLUTION_INSPECTION_TCSS],
)
def test_tcss_files_are_token_pure(tcss_path: Path) -> None:
    assert tcss_path.is_file()
    body = _strip_comments(tcss_path.read_text(encoding="utf-8"))
    assert not _HEX_RE.search(body), f"Hex literal found in {tcss_path.name}"
    assert not _RGB_RE.search(body), f"rgb()/rgba() literal in {tcss_path.name}"


# ---------------------------------------------------------------------------
# Message contracts
# ---------------------------------------------------------------------------


def test_overlay_closed_message_is_frozen() -> None:
    msg = OverlayClosedMessage(overlay_name="memory_review")
    with pytest.raises(dataclasses.FrozenInstanceError):
        msg.overlay_name = "other"  # type: ignore[misc]


def test_toast_request_message_has_message_and_urgency_fields() -> None:
    msg = ToastRequestMessage(message="hello", urgency="critical")
    assert msg.message == "hello"
    assert msg.urgency == "critical"
    assert dataclasses.is_dataclass(msg)


def test_open_evolution_inspection_message_payload() -> None:
    msg = OpenEvolutionInspectionMessage(
        owl_name="Athena", changed_traits={"verbosity": (0.5, 0.7)}
    )
    assert msg.owl_name == "Athena"
    assert msg.changed_traits["verbosity"] == (0.5, 0.7)


# ---------------------------------------------------------------------------
# Coordinator wiring for toast_request
# ---------------------------------------------------------------------------


class _FakeApp:
    def __init__(self) -> None:
        self.posted: list[Any] = []

    def call_from_thread(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        return fn(*args, **kwargs)

    def post_message(self, message: Any) -> None:
        self.posted.append(message)


@pytest.mark.asyncio
async def test_coordinator_dispatches_toast_request() -> None:
    from stackowl.events.bus import EventBus
    from stackowl.tui.coordinator import UIStateCoordinator

    app = _FakeApp()
    coord = UIStateCoordinator(app=app, event_bus=EventBus())  # type: ignore[arg-type]
    await coord._dispatch(
        "toast_request", {"message": "saved", "urgency": "low"}
    )
    assert len(app.posted) == 1
    msg = app.posted[0]
    assert isinstance(msg, ToastRequestMessage)
    assert msg.message == "saved"
    assert msg.urgency == "low"
