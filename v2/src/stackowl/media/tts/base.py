"""TtsBackend — the text-to-speech backend contract (E10-S3).

A backend turns ``text`` into a synthesized audio FILE on disk and returns a
:class:`TtsResult` carrying the PATH (never raw bytes — the agent reasons about
a path and ``send_file`` delivers it). Backends are LOCAL (self-hosted, no
egress) or CLOUD (the text leaves the machine → the tool discloses egress).

Self-hosted-first policy: a local OSS TTS engine is the default + only thing ON
by default; a cloud backend exists but is DISABLED unless a key is explicitly
configured. ``is_available()`` lets the selector skip a backend whose heavy dep
failed to install / whose voice failed to download WITHOUT raising (B5).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

__all__ = ["TtsAvailability", "TtsBackend", "TtsResult"]


class TtsResult(BaseModel):
    """The successful outcome of one synthesis — a PATH, never raw bytes."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str
    duration_ms: float
    voice: str
    backend: str
    is_local: bool


@dataclass(frozen=True)
class TtsAvailability:
    """Whether a backend can run right now, with a structured reason when not."""

    available: bool
    reason: str | None = None

    @classmethod
    def ok(cls) -> TtsAvailability:
        return cls(available=True, reason=None)

    @classmethod
    def no(cls, reason: str) -> TtsAvailability:
        return cls(available=False, reason=reason)


class TtsBackend(ABC):
    """Abstract text-to-speech backend (ARCH-94 sibling of the vision substrate).

    Implementations MUST NOT raise from :meth:`synthesize` for an operational
    failure — they return a structured ``str`` error instead (the selector/tool
    surface it). ``is_available`` likewise never raises.
    """

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def is_local(self) -> bool:
        """True → self-hosted (no egress); False → cloud (text leaves the box)."""
        ...

    @abstractmethod
    async def is_available(self, voice: str | None = None) -> TtsAvailability:
        """Report whether this backend can synthesize now. Never raises (B5).

        ``voice`` lets a backend that lazily fetches per-voice assets (e.g. the
        local engine) check readiness for the specific requested voice; backends
        that are voice-agnostic ignore it.
        """
        ...

    @abstractmethod
    async def synthesize(self, text: str, *, voice: str | None) -> TtsResult | str:
        """Synthesize ``text`` to an audio file.

        Returns a :class:`TtsResult` (path + metadata) on success, or a plain
        ``str`` describing the failure. NEVER raises for an operational error.
        """
        ...
