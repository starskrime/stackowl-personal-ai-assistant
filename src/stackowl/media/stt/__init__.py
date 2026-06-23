"""Speech-to-text (STT) media substrate — self-hosted-first, cloud opt-in.

The ARCH-94 sibling of the TTS substrate (:mod:`stackowl.media.tts`), but in the
opposite direction: audio bytes → text. A backend turns raw audio (e.g. an OGG
Telegram voice note, or a WAV captured from a mic) into a transcript string.

Self-hosted-first policy ([[feedback_self_hosted_only]]): the local OSS engine
(``openai-whisper``) is the default + the only thing ON by default; the audio
never leaves the box. A cloud backend seam exists in the selector but is disabled
for now (no cloud STT backend is shipped yet). ``is_available()`` lets the
selector skip a backend whose heavy dep failed to install WITHOUT raising (B5).
"""

from __future__ import annotations

from stackowl.media.stt.base import (
    SttAvailability,
    SttBackend,
    SttResult,
    stt_error_key,
)
from stackowl.media.stt.local import WhisperSttBackend
from stackowl.media.stt.selector import SttSelection, SttSelector

__all__ = [
    "SttAvailability",
    "SttBackend",
    "SttResult",
    "SttSelection",
    "SttSelector",
    "WhisperSttBackend",
    "stt_error_key",
]
