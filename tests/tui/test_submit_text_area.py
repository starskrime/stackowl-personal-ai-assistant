"""Story 6 — SubmitTextArea: Enter submits, Shift+Enter newlines.

Key-token behaviour was verified empirically against the pinned Textual version:
``run_test`` delivers ``event.key == "enter"`` for Enter and ``"shift+enter"``
for Shift+Enter, and the two are distinguishable.  These tests lock that in.
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.events.bus import EventBus
from stackowl.tui.app import StackOwlApp
from stackowl.tui.widgets.submit_text_area import SubmitTextArea

pytestmark = pytest.mark.tui


# ---------------------------------------------------------------------------
# A. Message payload
# ---------------------------------------------------------------------------


def test_submitted_message_carries_text() -> None:
    msg = SubmitTextArea.Submitted("payload text")
    assert msg.text == "payload text"


# ---------------------------------------------------------------------------
# B. Unit-ish key path — drive _on_key directly with fake key events
# ---------------------------------------------------------------------------


class _FakeKey:
    """Minimal stand-in for ``textual.events.Key`` exercising the handler."""

    def __init__(self, key: str) -> None:
        self.key = key
        self.prevented = False
        self.stopped = False

    def prevent_default(self) -> None:
        self.prevented = True

    def stop(self) -> None:
        self.stopped = True


@pytest.mark.asyncio
async def test_on_key_enter_posts_submitted_and_no_newline() -> None:
    ta = SubmitTextArea()
    ta.insert("hi")
    posted: list[Any] = []
    ta.post_message = posted.append  # type: ignore[method-assign]
    event = _FakeKey("enter")

    await ta._on_key(event)  # type: ignore[arg-type]

    assert event.prevented and event.stopped
    assert len(posted) == 1
    assert isinstance(posted[0], SubmitTextArea.Submitted)
    assert posted[0].text == "hi"
    # Enter must NOT insert a newline into the editor itself.
    assert "\n" not in ta.text


@pytest.mark.asyncio
async def test_on_key_shift_enter_inserts_newline_and_no_submit() -> None:
    ta = SubmitTextArea()
    ta.insert("a")
    posted: list[Any] = []
    ta.post_message = posted.append  # type: ignore[method-assign]
    event = _FakeKey("shift+enter")

    await ta._on_key(event)  # type: ignore[arg-type]

    assert event.prevented and event.stopped
    # No Submitted message was posted (a TextArea.Changed from the insert is fine).
    assert not any(isinstance(m, SubmitTextArea.Submitted) for m in posted)
    assert "\n" in ta.text


@pytest.mark.asyncio
async def test_on_key_regular_char_neither_submits_nor_specialcases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ta = SubmitTextArea()
    posted: list[Any] = []
    ta.post_message = posted.append  # type: ignore[method-assign]
    event = _FakeKey("x")

    # Stub the PARENT TextArea handler so we isolate OUR override's decision:
    # a regular key must reach the passthrough (super) branch without being
    # prevented/stopped or producing a Submitted.
    called: list[bool] = []

    async def _fake_parent_on_key(self: Any, _event: Any) -> None:
        called.append(True)

    monkeypatch.setattr(
        "textual.widgets.TextArea._on_key", _fake_parent_on_key, raising=True
    )

    await ta._on_key(event)  # type: ignore[arg-type]

    assert called == [True]  # fell through to parent
    assert not any(isinstance(m, SubmitTextArea.Submitted) for m in posted)
    assert not event.prevented
    assert not event.stopped


# ---------------------------------------------------------------------------
# C. Full pilot — mount the real app, type, press Enter / Shift+Enter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pilot_enter_submits_and_clears_shift_enter_newlines() -> None:
    bus = EventBus()
    submitted: list[str] = []
    bus.subscribe("compose_submitted", lambda p: submitted.append(p["text"]))

    app = StackOwlApp(event_bus=bus)
    async with app.run_test(size=(100, 40)) as pilot:
        editor = app.query_one("#compose_input", SubmitTextArea)
        editor.focus()
        await pilot.pause()

        # --- Enter submits "hi" and clears the editor ---
        await pilot.press("h", "i")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

        assert submitted == ["hi"]
        assert editor.text == ""

        # --- Shift+Enter inserts a newline and does NOT submit ---
        await pilot.press("a")
        await pilot.press("shift+enter")
        await pilot.press("b")
        await pilot.pause()

        assert "\n" in editor.text
        assert editor.text == "a\nb"
        # No additional submit happened.
        assert submitted == ["hi"]
