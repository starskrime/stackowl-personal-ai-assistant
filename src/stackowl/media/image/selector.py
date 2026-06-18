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
from stackowl.media.image.base import ImageBackend
from stackowl.media.image.cloud import CloudImageBackend
from stackowl.media.image.local_sdxl import LocalSdxlBackend
from stackowl.media.local_first import select_local_first

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

    @staticmethod
    def _unavailable_message(local_reason: str, cloud_reason: str) -> str:
        """Build the actionable all-unavailable message (per-modality wording)."""
        return (
            f"image generation unavailable — local GPU not available "
            f"({local_reason}) and no cloud image provider is configured "
            f"({cloud_reason}). Run on an x86+CUDA host with enough memory/disk "
            f"(local SDXL auto-installs on first use where the capability probe "
            f"clears) or enable + configure the cloud fallback "
            f"(image.cloud_enabled + image.cloud_api_key)."
        )

    async def select(self) -> ImageSelection:
        """Return the best available backend (local before cloud). Never raises.

        Delegates the local-first-then-cloud control flow to the shared
        :func:`select_local_first` (CFG-4 / F019); only this modality's backend
        factories, probe, and message wording are supplied here. 'auto' = local
        then cloud fallback; 'local' = local-only; 'cloud' = cloud-only.
        """
        result = await select_local_first(
            engine=self._settings.engine,
            local_probe=self._local.is_available,
            cloud_probe=self._cloud.is_available,
            local_factory=lambda: self._local,
            cloud_factory=lambda: self._cloud,
            unavailable=self._unavailable_message,
            local_only_engine_reason="cloud fallback disabled (engine='local')",
        )
        if result.backend is not None:
            return ImageSelection.found(result.backend)
        return ImageSelection.unavailable(result.reason or "image generation unavailable")
