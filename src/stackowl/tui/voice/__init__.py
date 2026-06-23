"""Terminal-UI voice capture (push-to-talk dictation).

The mic-capture half of voice transcription: the TUI records a short audio clip,
hands the bytes to the shared :mod:`stackowl.media.stt` selector, and drops the
transcript into the compose box for the user to edit and send.
"""

from __future__ import annotations

from stackowl.tui.voice.recorder import MicRecorder, ShellMicRecorder

__all__ = ["MicRecorder", "ShellMicRecorder"]
