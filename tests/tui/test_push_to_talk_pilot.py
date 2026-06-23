"""INTEGRATION pilot — TUI push-to-talk (Ctrl+R) dictation.

Spins a real Textual app and drives the Ctrl+R binding with a stub recorder +
stub STT backend (never a real mic or Whisper model). Proves the two integration
truths a unit test can't:

1. record → transcribe DROPS the transcript into the compose box and does NOT
   auto-submit it (no ``compose_submitted`` event is emitted — the user edits then
   presses Enter);
2. when no mic tool is present the binding degrades to a status line — the compose
   box stays empty and nothing crashes.
"""

from __future__ import annotations

import pytest

from stackowl.config.settings import TranscriptionSettings
from stackowl.events.bus import EventBus
from stackowl.media.stt.base import SttAvailability, SttBackend, SttResult
from stackowl.media.stt.selector import SttSelector
from stackowl.tui.app import StackOwlApp
from stackowl.tui.widgets.submit_text_area import SubmitTextArea

pytestmark = pytest.mark.tui


class _StubBackend(SttBackend):
    def __init__(self, text: str) -> None:
        self._text = text

    @property
    def name(self) -> str:
        return "stub"

    @property
    def is_local(self) -> bool:
        return True

    async def is_available(self) -> SttAvailability:
        return SttAvailability.ok()

    async def transcribe(
        self, audio_bytes: bytes, *, audio_format: str = "ogg"
    ) -> SttResult | str:
        return SttResult(text=self._text, backend="stub", is_local=True)


class _StubRecorder:
    """Push-to-talk stub — yields fixed bytes; ``available`` toggles the no-mic path."""

    def __init__(self, *, available: bool = True, audio: bytes = b"wavbytes") -> None:
        self._available = available
        self._audio = audio
        self.started = False
        self.stopped = False

    def is_available(self) -> bool:
        return self._available

    async def start(self) -> bool:
        self.started = True
        return self._available

    async def stop(self) -> bytes:
        self.stopped = True
        return self._audio


def _selector(text: str) -> SttSelector:
    return SttSelector(TranscriptionSettings(enabled=True), local=_StubBackend(text))


@pytest.mark.asyncio
async def test_dictation_fills_compose_without_submitting() -> None:
    bus = EventBus()
    submitted: list[object] = []
    bus.subscribe("compose_submitted", submitted.append)

    recorder = _StubRecorder(audio=b"clip")
    app = StackOwlApp(bus, recorder=recorder, stt_selector=_selector("hello there"))
    async with app.run_test(size=(100, 40)) as pilot:
        app.query_one("#compose_input", SubmitTextArea).focus()
        await pilot.pause()
        await pilot.press("ctrl+r")  # start recording
        await pilot.pause()
        await pilot.press("ctrl+r")  # stop → transcribe → fill box
        await pilot.pause()

        editor = app.query_one("#compose_input", SubmitTextArea)
        assert editor.text == "hello there"
        assert recorder.started and recorder.stopped
        # The transcript was placed for editing — NOT submitted as a turn.
        assert submitted == []


@pytest.mark.asyncio
async def test_mic_button_click_dictates() -> None:
    # The VISIBLE 🎤 button drives the same path as Ctrl+R (mouse, not keyboard).
    bus = EventBus()
    submitted: list[object] = []
    bus.subscribe("compose_submitted", submitted.append)

    recorder = _StubRecorder(audio=b"clip")
    app = StackOwlApp(bus, recorder=recorder, stt_selector=_selector("clicked hello"))
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        await pilot.click("#compose_mic")  # start recording
        await pilot.pause()
        await pilot.click("#compose_mic")  # stop → transcribe → fill box
        await pilot.pause()

        editor = app.query_one("#compose_input", SubmitTextArea)
        assert editor.text == "clicked hello"
        assert recorder.started and recorder.stopped
        assert submitted == []  # filled for editing, NOT submitted


@pytest.mark.asyncio
async def test_no_mic_degrades_to_empty_box() -> None:
    bus = EventBus()
    recorder = _StubRecorder(available=False)
    app = StackOwlApp(bus, recorder=recorder, stt_selector=_selector("ignored"))
    async with app.run_test(size=(100, 40)) as pilot:
        app.query_one("#compose_input", SubmitTextArea).focus()
        await pilot.pause()
        await pilot.press("ctrl+r")
        await pilot.pause()

        editor = app.query_one("#compose_input", SubmitTextArea)
        assert editor.text == ""
        assert recorder.started is False  # never started — unavailable short-circuits


@pytest.mark.asyncio
async def test_dictation_noop_when_unconfigured() -> None:
    # recorder + selector both None (transcription disabled) → Ctrl+R is a no-op.
    bus = EventBus()
    app = StackOwlApp(bus)
    async with app.run_test(size=(100, 40)) as pilot:
        app.query_one("#compose_input", SubmitTextArea).focus()
        await pilot.pause()
        await pilot.press("ctrl+r")
        await pilot.pause()
        assert app.query_one("#compose_input", SubmitTextArea).text == ""
