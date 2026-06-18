"""ImageBackend — the image-generation backend contract (E10-S4).

A backend turns a text ``prompt`` into a generated image FILE on disk and returns
an :class:`ImageResult` carrying the PATH (never raw bytes — the agent reasons
about a path and ``send_file`` delivers it). Backends are LOCAL (self-hosted, no
egress) or CLOUD (the prompt leaves the machine → the tool discloses egress).

Self-hosted-first policy: a local OSS image model is preferred ONLY where a
capability probe clears (x86 + CUDA + enough memory + disk). On an incapable host
(e.g. a Tegra unified-memory board) local generation is NOT viable, so the
selector falls back to a cloud backend IF a key is configured, else returns a
structured "unavailable". ``is_available()`` lets the selector skip a backend
whose probe failed / whose heavy dep failed to install WITHOUT raising (B5).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

__all__ = ["ImageAvailability", "ImageBackend", "ImageResult"]


class ImageResult(BaseModel):
    """The successful outcome of one generation — a PATH, never raw bytes."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str
    size: str
    backend: str
    is_local: bool


@dataclass(frozen=True)
class ImageAvailability:
    """Whether a backend can run right now, with a structured reason when not."""

    available: bool
    reason: str | None = None

    @classmethod
    def ok(cls) -> ImageAvailability:
        return cls(available=True, reason=None)

    @classmethod
    def no(cls, reason: str) -> ImageAvailability:
        return cls(available=False, reason=reason)


class ImageBackend(ABC):
    """Abstract image-generation backend (ARCH-94 sibling of the tts substrate).

    Implementations MUST NOT raise from :meth:`generate` for an operational
    failure — they return a structured ``str`` error instead (the selector/tool
    surface it). ``is_available`` likewise never raises.
    """

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def is_local(self) -> bool:
        """True → self-hosted (no egress); False → cloud (prompt leaves the box)."""
        ...

    @abstractmethod
    async def is_available(self) -> ImageAvailability:
        """Report whether this backend can generate now. Never raises (B5)."""
        ...

    @abstractmethod
    async def generate(self, prompt: str, *, size: str | None = None) -> ImageResult | str:
        """Generate an image from ``prompt`` to an image file.

        Returns an :class:`ImageResult` (path + metadata) on success, or a plain
        ``str`` describing the failure. NEVER raises for an operational error.
        """
        ...
