"""TtsSelector — pick a TTS backend LOCAL-FIRST, cloud only as opt-in fallback.

Self-hosted-first policy ([[feedback_self_hosted_only]]): the local OSS engine is
preferred whenever it can initialize (the text stays on the box). The cloud
backend is used ONLY when the local engine is unavailable AND the cloud fallback
is explicitly enabled + configured. When nothing can run the selector returns a
structured, ACTIONABLE "unavailable" — it NEVER raises (B5), so the tool degrades
gracefully on a host where the engine failed to install and no cloud is set up.

Built from :class:`TtsSettings` so config drives engine choice / voice / the
opt-in cloud reference; backends are injectable for deterministic tests.
"""

from __future__ import annotations

from dataclasses import dataclass

from stackowl.config.settings import TtsSettings
from stackowl.media.local_first import select_local_first
from stackowl.media.tts.base import TtsAvailability, TtsBackend
from stackowl.media.tts.cloud import CloudTtsBackend
from stackowl.media.tts.piper import PiperBackend

__all__ = ["TtsSelection", "TtsSelector"]


@dataclass(frozen=True)
class TtsSelection:
    """The outcome of backend selection.

    Exactly one of ``backend`` (available) or ``reason`` (unavailable) is set.
    """

    backend: TtsBackend | None
    reason: str | None

    @property
    def available(self) -> bool:
        return self.backend is not None

    @classmethod
    def found(cls, backend: TtsBackend) -> TtsSelection:
        return cls(backend=backend, reason=None)

    @classmethod
    def unavailable(cls, reason: str) -> TtsSelection:
        return cls(backend=None, reason=reason)


class TtsSelector:
    """Selects a TTS backend local-first; structured-unavailable when none run."""

    def __init__(
        self,
        settings: TtsSettings,
        *,
        local: TtsBackend | None = None,
        cloud: TtsBackend | None = None,
    ) -> None:
        self._settings = settings
        # Backends are injectable (tests pass fakes); else build from settings.
        self._local = local or PiperBackend(
            voice=settings.voice,
            voice_base_url=settings.voice_base_url,
        )
        self._cloud = cloud or CloudTtsBackend(
            base_url=settings.cloud_base_url,
            api_key_ref=settings.cloud_api_key if settings.cloud_enabled else "",
            voice=settings.cloud_voice,
            model=settings.cloud_model,
        )

    @staticmethod
    def _unavailable_message(local_reason: str, cloud_reason: str) -> str:
        """Build the actionable all-unavailable message (per-modality wording)."""
        return (
            f"tts unavailable — the local OSS TTS engine failed to initialize "
            f"({local_reason}) and no cloud TTS is available ({cloud_reason}). "
            f"Install the local engine (it auto-installs on first use; check the "
            f"logs for the install error) or enable + configure the cloud fallback "
            f"(tts.cloud_enabled + tts.cloud_api_key)."
        )

    async def select(self, *, voice: str | None = None) -> TtsSelection:
        """Return the best available backend (local before cloud). Never raises.

        Delegates the local-first-then-cloud control flow to the shared
        :func:`select_local_first` (CFG-4 / F019); only this modality's backend
        factories, probe (closing over ``voice``), and message wording are
        supplied here. 'auto' = local then cloud fallback; 'piper' = local-only.
        """
        async def _local_probe() -> TtsAvailability:
            return await self._local.is_available(voice)

        async def _cloud_probe() -> TtsAvailability:
            return await self._cloud.is_available()

        result = await select_local_first(
            engine=self._settings.engine,
            local_probe=_local_probe,
            cloud_probe=_cloud_probe,
            local_factory=lambda: self._local,
            cloud_factory=lambda: self._cloud,
            unavailable=self._unavailable_message,
            local_only_engine_reason="cloud fallback disabled (engine='piper')",
        )
        if result.backend is not None:
            return TtsSelection.found(result.backend)
        return TtsSelection.unavailable(result.reason or "tts unavailable")
