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
from stackowl.infra.observability import log
from stackowl.media.tts.base import TtsBackend
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

    async def select(self, *, voice: str | None = None) -> TtsSelection:
        """Return the best available backend (local before cloud). Never raises.

        The local engine is tried first; the cloud fallback is consulted ONLY when
        the local engine is unavailable AND the engine setting permits a fallback
        ('auto'). 'piper' = local-only (cloud never used even if configured).
        """
        # 1. ENTRY
        log.tool.debug(
            "[tts.selector] select: entry",
            extra={"_fields": {"engine": self._settings.engine}},
        )

        # 2. LOCAL-FIRST — prefer the self-hosted engine (text stays on the box).
        local_avail = await self._local.is_available(voice)
        if local_avail.available:
            log.tool.info("[tts.selector] select: chose LOCAL engine")
            return TtsSelection.found(self._local)
        local_reason = local_avail.reason or "local TTS engine unavailable"
        log.tool.info(
            "[tts.selector] select: local engine unavailable",
            extra={"_fields": {"reason": local_reason}},
        )

        # 3. CLOUD FALLBACK — opt-in only, and only when engine='auto'.
        if self._settings.engine == "auto":
            cloud_avail = await self._cloud.is_available()
            if cloud_avail.available:
                log.tool.info("[tts.selector] select: chose CLOUD fallback (egress)")
                return TtsSelection.found(self._cloud)
            cloud_reason = cloud_avail.reason or "cloud TTS unavailable"
        else:
            cloud_reason = "cloud fallback disabled (engine='piper')"

        # 4. EXIT — nothing available → actionable, structured unavailable.
        log.tool.info("[tts.selector] select: no TTS backend available")
        return TtsSelection.unavailable(
            f"tts unavailable — the local OSS TTS engine failed to initialize "
            f"({local_reason}) and no cloud TTS is available ({cloud_reason}). "
            f"Install the local engine (it auto-installs on first use; check the "
            f"logs for the install error) or enable + configure the cloud fallback "
            f"(tts.cloud_enabled + tts.cloud_api_key)."
        )
