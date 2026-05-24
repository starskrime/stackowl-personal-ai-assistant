"""Overlay keyboard reachability — every overlay must offer an escape route."""

from __future__ import annotations

import pytest

from stackowl.tui.widgets.evolution_inspection_panel import EvolutionInspectionPanel
from stackowl.tui.widgets.memory_review_panel import MemoryReviewPanel
from stackowl.tui.widgets.overlay_panel import OverlayPanel
from stackowl.tui.widgets.toast_notification import ToastNotification

pytestmark = pytest.mark.tui


def test_overlay_has_escape_binding() -> None:
    panel = OverlayPanel()
    binding_keys = [b.key for b in panel.BINDINGS]
    assert "escape" in binding_keys


def test_critical_toast_has_dismiss_bindings() -> None:
    toast = ToastNotification("critical message", "critical")
    keys = [b.key for b in toast.BINDINGS]
    assert "d" in keys
    assert "enter" in keys


def test_memory_review_panel_has_escape_binding() -> None:
    panel = MemoryReviewPanel()
    keys = [b.key for b in panel.BINDINGS]
    assert "escape" in keys


def test_evolution_inspection_panel_has_escape_binding() -> None:
    panel = EvolutionInspectionPanel()
    keys = [b.key for b in panel.BINDINGS]
    assert "escape" in keys
