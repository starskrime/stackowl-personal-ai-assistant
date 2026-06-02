"""ImageSelector — pick an image backend LOCAL-FIRST, cloud only as opt-in fallback.

Self-hosted-first policy ([[feedback_self_hosted_only]]): the local OSS image model
is preferred whenever the capability probe clears AND it can initialize (the prompt
stays on the box). On an incapable host (e.g. Tegra/unified-memory) the probe says
no, so the cloud backend is used ONLY when local is unavailable AND the cloud
fallback is explicitly enabled + configured. When nothing can run the selector
returns a structured, ACTIONABLE "unavailable" — it NEVER raises (B5), so the tool
degrades gracefully where local SDXL is not viable and no cloud is set up.

Built from :class:`ImageSettings` so config drives engine choice / model / the
opt-in cloud reference; backends are injectable for deterministic tests.
"""

from __future__ import annotations

from dataclasses import dataclass

from stackowl.config.settings import ImageSettings
from stackowl.infra.observability import log
from stackowl.media.image.base import ImageBackend
from stackowl.media.image.cloud import CloudImageBackend
from stackowl.media.image.local_sdxl import LocalSdxlBackend

__all__ = ["ImageSelection", "ImageSelector"]


@dataclass(frozen=True)
class ImageSelection:
    """The outcome of backend selection.

    Exactly one of ``backend`` (available) or ``reason`` (unavailable) is set.
    """

    backend: ImageBackend | None
    reason: str | None

    @property
    def available(self) -> bool:
        return self.backend is not None

    @classmethod
    def found(cls, backend: ImageBackend) -> ImageSelection:
        return cls(backend=backend, reason=None)

    @classmethod
    def unavailable(cls, reason: str) -> ImageSelection:
        return cls(backend=None, reason=reason)


class ImageSelector:
    """Selects an image backend local-first; structured-unavailable when none run."""

    def __init__(
        self,
        settings: ImageSettings,
        *,
        local: ImageBackend | None = None,
        cloud: ImageBackend | None = None,
    ) -> None:
        self._settings = settings
        # Backends are injectable (tests pass fakes); else build from settings.
        self._local = local or LocalSdxlBackend(
            model=settings.model,
            default_size=settings.size,
        )
        self._cloud = cloud or CloudImageBackend(
            base_url=settings.cloud_base_url,
            api_key_ref=settings.cloud_api_key if settings.cloud_enabled else "",
            model=settings.cloud_model,
            default_size=settings.size,
        )

    async def select(self) -> ImageSelection:
        """Return the best available backend (local before cloud). Never raises.

        The local engine is tried first (its probe gates the heavy install); the
        cloud fallback is consulted ONLY when local is unavailable AND the engine
        setting permits a fallback ('auto'). 'local' = local-only (cloud never
        used even if configured); 'cloud' = skip local, cloud-only.
        """
        # 1. ENTRY
        log.tool.debug(
            "[image.selector] select: entry",
            extra={"_fields": {"engine": self._settings.engine}},
        )

        # engine='cloud' → skip the local probe entirely, go straight to cloud.
        if self._settings.engine == "cloud":
            cloud_avail = await self._cloud.is_available()
            if cloud_avail.available:
                log.tool.info("[image.selector] select: chose CLOUD (engine='cloud', egress)")
                return ImageSelection.found(self._cloud)
            return ImageSelection.unavailable(
                f"image generation unavailable — engine is set to 'cloud' but the "
                f"cloud backend is unavailable ({cloud_avail.reason or 'not configured'}). "
                f"Enable + configure it (image.cloud_enabled + image.cloud_api_key)."
            )

        # 2. LOCAL-FIRST — prefer the self-hosted model (prompt stays on the box).
        local_avail = await self._local.is_available()
        if local_avail.available:
            log.tool.info("[image.selector] select: chose LOCAL model")
            return ImageSelection.found(self._local)
        local_reason = local_avail.reason or "local image model unavailable"
        log.tool.info(
            "[image.selector] select: local model unavailable",
            extra={"_fields": {"reason": local_reason}},
        )

        # 3. CLOUD FALLBACK — opt-in only, and only when engine='auto'.
        if self._settings.engine == "auto":
            cloud_avail = await self._cloud.is_available()
            if cloud_avail.available:
                log.tool.info("[image.selector] select: chose CLOUD fallback (egress)")
                return ImageSelection.found(self._cloud)
            cloud_reason = cloud_avail.reason or "cloud image generation unavailable"
        else:
            cloud_reason = "cloud fallback disabled (engine='local')"

        # 4. EXIT — nothing available → actionable, structured unavailable.
        log.tool.info("[image.selector] select: no image backend available")
        return ImageSelection.unavailable(
            f"image generation unavailable — local GPU not available "
            f"({local_reason}) and no cloud image provider is configured "
            f"({cloud_reason}). Run on an x86+CUDA host with enough memory/disk "
            f"(local SDXL auto-installs on first use where the capability probe "
            f"clears) or enable + configure the cloud fallback "
            f"(image.cloud_enabled + image.cloud_api_key)."
        )
