"""SttBackend — the speech-to-text backend contract.

A backend turns raw ``audio_bytes`` into a transcript and returns an
:class:`SttResult` carrying the TEXT. Backends are LOCAL (self-hosted, no egress)
or CLOUD (the audio leaves the machine). Self-hosted-first policy: a local OSS
STT engine is the default + only thing ON by default; a cloud backend may exist
but is DISABLED unless explicitly configured.

Mirrors :class:`stackowl.media.tts.base.TtsBackend` (the established media-backend
contract): :meth:`transcribe` MUST NOT raise for an operational failure — it
returns a structured ``str`` error instead, which the caller surfaces.
``is_available()`` likewise never raises (B5).

An EMPTY transcript is a valid SUCCESS, not an error — ``SttResult(text="")``
means "the audio was processed but nothing intelligible was heard". Callers
distinguish the two by type: ``isinstance(result, str)`` → failure;
``result.text == ""`` → heard nothing.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

__all__ = ["SttAvailability", "SttBackend", "SttResult"]


class SttResult(BaseModel):
    """The successful outcome of one transcription — the recognized TEXT."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str
    backend: str
    is_local: bool


@dataclass(frozen=True)
class SttAvailability:
    """Whether a backend can run right now, with a structured reason when not."""

    available: bool
    reason: str | None = None

    @classmethod
    def ok(cls) -> SttAvailability:
        return cls(available=True, reason=None)

    @classmethod
    def no(cls, reason: str) -> SttAvailability:
        return cls(available=False, reason=reason)


class SttBackend(ABC):
    """Abstract speech-to-text backend (sibling of the TTS/vision substrates).

    Implementations MUST NOT raise from :meth:`transcribe` for an operational
    failure — they return a structured ``str`` error instead. ``is_available``
    likewise never raises.
    """

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def is_local(self) -> bool:
        """True → self-hosted (no egress); False → cloud (audio leaves the box)."""
        ...

    @abstractmethod
    async def is_available(self) -> SttAvailability:
        """Report whether this backend can transcribe now. Never raises (B5)."""
        ...

    @abstractmethod
    async def transcribe(
        self, audio_bytes: bytes, *, audio_format: str = "ogg"
    ) -> SttResult | str:
        """Transcribe ``audio_bytes`` to text.

        Args:
            audio_bytes: Raw audio content (OGG from Telegram, WAV from a mic, …).
            audio_format: The container/extension hint (``"ogg"``, ``"wav"``, …)
                used to name the tempfile the engine reads.

        Returns:
            An :class:`SttResult` on success (``text`` may be empty when nothing
            was heard), or a plain ``str`` describing an operational failure.
            NEVER raises for an operational error.
        """
        ...
