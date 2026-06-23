"""SttSelector — pick an STT backend LOCAL-FIRST, cloud only as opt-in fallback.

Self-hosted-first policy ([[feedback_self_hosted_only]]): the local OSS engine
(Whisper) is preferred whenever it can initialize (the audio stays on the box).
No cloud STT backend ships yet, so 'auto' still resolves to local; the cloud seam
exists only so a future :class:`SttBackend` can be slotted in without touching
callers. When nothing can run the selector returns a structured, ACTIONABLE
"unavailable" — it NEVER raises (B5), so the Telegram/TUI voice paths degrade
gracefully on a host where the engine failed to install.

Built from :class:`TranscriptionSettings`; backends are injectable for tests.
"""

from __future__ import annotations

from dataclasses import dataclass

from stackowl.config.settings import TranscriptionSettings
from stackowl.media.local_first import select_local_first
from stackowl.media.stt.base import SttAvailability, SttBackend, SttResult
from stackowl.media.stt.local import WhisperSttBackend

__all__ = ["SttSelection", "SttSelector"]


@dataclass(frozen=True)
class SttSelection:
    """The outcome of backend selection.

    Exactly one of ``backend`` (available) or ``reason`` (unavailable) is set.
    """

    backend: SttBackend | None
    reason: str | None

    @property
    def available(self) -> bool:
        return self.backend is not None

    @classmethod
    def found(cls, backend: SttBackend) -> SttSelection:
        return cls(backend=backend, reason=None)

    @classmethod
    def unavailable(cls, reason: str) -> SttSelection:
        return cls(backend=None, reason=reason)


class _UnavailableCloudBackend(SttBackend):
    """Placeholder cloud backend — always unavailable (no cloud STT ships yet).

    Keeps the local-first control flow honest: 'auto' probes this, gets a clear
    "not configured" reason, and falls through to a structured-unavailable result
    rather than special-casing the missing cloud path in the selector.
    """

    @property
    def name(self) -> str:
        return "cloud-stt"

    @property
    def is_local(self) -> bool:
        return False

    async def is_available(self) -> SttAvailability:
        return SttAvailability.no("no cloud STT backend is configured")

    async def transcribe(
        self, audio_bytes: bytes, *, audio_format: str = "ogg"
    ) -> SttResult | str:
        return "cloud STT is not available"


class SttSelector:
    """Selects an STT backend local-first; structured-unavailable when none run."""

    def __init__(
        self,
        settings: TranscriptionSettings,
        *,
        local: SttBackend | None = None,
        cloud: SttBackend | None = None,
    ) -> None:
        self._settings = settings
        # Backends are injectable (tests pass fakes); else build from settings.
        self._local = local or WhisperSttBackend(model_name=settings.model)
        self._cloud = cloud or _UnavailableCloudBackend()

    @staticmethod
    def _unavailable_message(local_reason: str, cloud_reason: str) -> str:
        """Build the actionable all-unavailable message."""
        return (
            f"transcription unavailable — the local Whisper engine failed to "
            f"initialize ({local_reason}) and no cloud STT is available "
            f"({cloud_reason}). Check the logs for the model-load error (a smaller "
            f"transcription.model like 'tiny' may help on a constrained host)."
        )

    async def select(self) -> SttSelection:
        """Return the best available backend (local before cloud). Never raises.

        Delegates the local-first-then-cloud control flow to the shared
        :func:`select_local_first`; only this modality's factories, probes, and
        message wording are supplied here. 'auto' = local then cloud fallback;
        'local' = local-only.
        """
        async def _local_probe() -> SttAvailability:
            return await self._local.is_available()

        async def _cloud_probe() -> SttAvailability:
            return await self._cloud.is_available()

        result = await select_local_first(
            engine=self._settings.engine,
            local_probe=_local_probe,
            cloud_probe=_cloud_probe,
            local_factory=lambda: self._local,
            cloud_factory=lambda: self._cloud,
            unavailable=self._unavailable_message,
            local_only_engine_reason="cloud fallback disabled (engine='local')",
        )
        if result.backend is not None:
            return SttSelection.found(result.backend)
        return SttSelection.unavailable(result.reason or "transcription unavailable")
