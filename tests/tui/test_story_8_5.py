"""Story 8.5 part A — OverlayPanel base + OverlayQueue.

Toast, EvolutionBadge / EvolutionInspectionPanel, TCSS purity and the
coordinator wiring live in ``test_story_8_5b.py`` to keep each file under
the 300-line limit (B2).
"""

from __future__ import annotations

import gc
from typing import Any

import pytest

from stackowl.memory.models import StagedFact
from stackowl.tui.messages import OverlayClosedMessage
from stackowl.tui.widgets.overlay_panel import OverlayPanel, OverlayQueue

pytestmark = pytest.mark.tui


# ---------------------------------------------------------------------------
# Helpers (shared)
# ---------------------------------------------------------------------------


class _DummyPanel(OverlayPanel):
    overlay_name = "dummy"


class _FocusableStub:
    def __init__(self) -> None:
        self.focus_calls = 0

    def focus(self) -> None:
        self.focus_calls += 1


def _staged_fact(
    content: str = "fact A", source_type: str = "conversation"
) -> StagedFact:
    return StagedFact(
        content=content,
        source_type=source_type,  # type: ignore[arg-type]
        source_ref="ref-1",
        confidence=0.85,
    )


def _set_timer_recorder(
    monkeypatch: pytest.MonkeyPatch, widget: Any
) -> list[tuple[float, Any]]:
    """Replace ``widget.set_timer`` with a recorder and return the call log."""
    calls: list[tuple[float, Any]] = []

    def _record(delay: float, callback: Any) -> Any:
        calls.append((delay, callback))
        return object()

    monkeypatch.setattr(widget, "set_timer", _record, raising=False)
    return calls


# ---------------------------------------------------------------------------
# 1-5. OverlayPanel behaviour
# ---------------------------------------------------------------------------


def test_overlay_open_overlay_sets_display_true() -> None:
    panel = _DummyPanel()
    panel.display = False
    panel.open_overlay()
    assert panel.display is True


def test_overlay_close_sets_display_false_and_posts_closed_message() -> None:
    panel = _DummyPanel()
    posted: list[Any] = []
    panel.post_message = posted.append  # type: ignore[method-assign]
    panel.display = True
    panel.close()
    assert panel.display is False
    assert any(isinstance(m, OverlayClosedMessage) for m in posted)
    assert posted[0].overlay_name == "dummy"


def test_overlay_close_restores_prior_focus_via_weakref() -> None:
    panel = _DummyPanel()
    panel.post_message = lambda _msg: None  # type: ignore[assignment]
    prior = _FocusableStub()
    panel.open_overlay(prior_focused=prior)  # type: ignore[arg-type]
    panel.close()
    assert prior.focus_calls == 1


def test_overlay_close_handles_dead_weakref_gracefully() -> None:
    panel = _DummyPanel()
    panel.post_message = lambda _msg: None  # type: ignore[assignment]
    prior = _FocusableStub()
    panel.open_overlay(prior_focused=prior)  # type: ignore[arg-type]
    # Drop the strong reference — the weakref inside the panel goes dead.
    del prior
    gc.collect()
    panel.close()  # must not raise


def test_overlay_panel_has_escape_binding() -> None:
    panel = _DummyPanel()
    binding_keys = [b.key for b in panel.BINDINGS]
    assert "escape" in binding_keys


# ---------------------------------------------------------------------------
# 6-7. OverlayQueue sequencing
# ---------------------------------------------------------------------------


def test_overlay_queue_push_opens_immediately_when_empty() -> None:
    queue = OverlayQueue()
    panel = _DummyPanel()
    panel.display = False
    queue.push(panel)
    assert queue.active is panel
    assert panel.display is True


def test_overlay_queue_on_closed_advances_to_next_queued_overlay() -> None:
    queue = OverlayQueue()
    first = _DummyPanel()
    second = _DummyPanel()
    second.display = False
    queue.push(first)
    queue.push(second)
    assert queue.active is first
    assert queue.pending == 1
    queue.on_closed(first)
    assert queue.active is second
    assert second.display is True


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
