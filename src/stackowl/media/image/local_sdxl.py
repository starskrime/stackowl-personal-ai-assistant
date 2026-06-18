"""LocalSdxlBackend — the local OSS image model (E10-S4), probe-gated.

Mirrors :class:`PiperBackend` (the lazy-load idiom) with one critical addition:
the capability PROBE gates EVERYTHING. ``is_available()`` runs
:meth:`ImageCapability.probe` FIRST and ONLY proceeds to the multi-GB
``torch``/``diffusers`` auto-install + weight download + model load when the probe
says ``can_run_local`` (Fork C — probe-before-install). If the probe says no,
``is_available()`` returns the probe reason and NEVER attempts the heavy install.

When the probe clears, on the first generate the heavy pip packages are
AUTO-INSTALLED ([[feedback_agent_auto_install]]) — guarded by
``TestModeGuard.assert_not_test_mode`` so no install/network/generate ever happens
under test mode — the pipeline is loaded into ``models_dir()``, and generation
(GPU work) runs via ``run_in_executor`` so the event loop stays free. It writes a
PNG into ``media_dir()/image/`` and returns the PATH — never raw bytes.
is_local=True → no egress. Any failure → ``is_available()``/``generate`` degrade
to a structured reason, never a crash (B5).
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

from stackowl.config.test_mode import TestModeGuard
from stackowl.infra.observability import log
from stackowl.media.image.base import ImageAvailability, ImageBackend, ImageResult
from stackowl.media.image.capability import ImageCapability
from stackowl.paths import StackowlHome

__all__ = ["LocalSdxlBackend"]

# Heavy pip package ids for the local image pipeline (real dependencies, not a
# vendor attribution in logic). Installed AT RUNTIME on a capable host only —
# never declared in pyproject so ``uv sync`` stays green on the Jetson.
_PIP_PACKAGES = ("diffusers", "torch", "transformers", "accelerate", "safetensors")
# The default OSS image model id, fetched on first use into the durable models
# dir. Overridable via settings for an air-gapped / self-hosted mirror.
_DEFAULT_MODEL = "stabilityai/sdxl-turbo"
_DEFAULT_SIZE = "1024x1024"
_INSTALL_TIMEOUT_S = 1800


class LocalSdxlBackend(ImageBackend):
    """Local OSS image model: probe-gated lazy-load + auto-install, off the loop."""

    def __init__(
        self,
        *,
        model: str = _DEFAULT_MODEL,
        default_size: str = _DEFAULT_SIZE,
    ) -> None:
        self._model = model or _DEFAULT_MODEL
        self._default_size = default_size or _DEFAULT_SIZE
        self._pipeline: Any = None  # the loaded diffusion pipeline, lazily loaded
        self._unavailable_reason: str | None = None
        log.tool.debug(
            "[image.local] init",
            extra={"_fields": {"model": self._model}},
        )

    @property
    def name(self) -> str:
        return "local-sdxl"

    @property
    def is_local(self) -> bool:
        return True

    # ------------------------------------------------------------- availability
    async def is_available(self) -> ImageAvailability:
        """Probe FIRST; only if viable lazily load. Never raises (B5)."""
        # THE GATE (Fork C): the probe runs BEFORE any install. If it says no,
        # we return its reason and NEVER touch the multi-GB install path.
        probe = ImageCapability.probe()
        if not probe.can_run_local:
            log.tool.info(
                "[image.local] is_available: probe says local SDXL not viable",
                extra={"_fields": {"reason": probe.reason}},
            )
            return ImageAvailability.no(
                f"local image generation not available ({probe.reason})"
            )
        loop = asyncio.get_event_loop()
        try:
            return await loop.run_in_executor(None, self._ensure_loaded)
        except Exception as exc:  # defense in depth — _ensure_loaded already catches.
            log.tool.error("[image.local] is_available: unexpected failure", exc_info=exc)
            return ImageAvailability.no(
                f"local image model unavailable: {type(exc).__name__}"
            )

    def _models_dir(self) -> Path:
        return StackowlHome.models_dir() / "image"

    def _ensure_loaded(self) -> ImageAvailability:
        """Install the deps + load the pipeline. Sync (executor)."""
        if self._pipeline is not None:
            return ImageAvailability.ok()
        if self._unavailable_reason is not None:
            # Negative cache: a prior install/load failure persists this process.
            # Self-heal on restart (a fresh process re-attempts).
            return ImageAvailability.no(
                f"local image model could not initialize ({self._unavailable_reason})"
            )
        try:
            # No install/network/GPU load under test mode — gate BEFORE any dep
            # import or from_pretrained, regardless of whether deps are already
            # importable. The TestModeViolation is caught below → structured no.
            TestModeGuard.assert_not_test_mode("image.local")
            pipeline_cls, torch_mod = self._import_pipeline()
            cache_dir = self._models_dir()
            cache_dir.mkdir(parents=True, exist_ok=True)
            log.tool.debug(
                "[image.local] _ensure_loaded: loading pipeline",
                extra={"_fields": {"model": self._model}},
            )
            pipe = pipeline_cls.from_pretrained(
                self._model,
                torch_dtype=torch_mod.float16,
                cache_dir=str(cache_dir),
            )
            self._pipeline = pipe.to("cuda")
            log.tool.info(
                "[image.local] _ensure_loaded: pipeline ready",
                extra={"_fields": {"model": self._model}},
            )
            return ImageAvailability.ok()
        except Exception as exc:  # install/load failure → structured, never raise.
            self._unavailable_reason = f"{type(exc).__name__}: {exc}"
            log.tool.error(
                "[image.local] _ensure_loaded: failed — backend unavailable",
                exc_info=exc,
                extra={"_fields": {"model": self._model}},
            )
            return ImageAvailability.no(
                f"local image model could not initialize ({self._unavailable_reason})"
            )

    def _import_pipeline(self) -> tuple[Any, Any]:
        """Import the diffusion pipeline + torch, auto-installing the deps once."""
        try:
            import torch
            from diffusers import AutoPipelineForText2Image

            return AutoPipelineForText2Image, torch
        except ImportError:
            # Test mode is already gated at the top of _ensure_loaded (before this
            # is reached), so an auto-install only ever runs on a real host.
            log.tool.info(
                "[image.local] _import_pipeline: deps missing — auto-installing",
                extra={"_fields": {"packages": list(_PIP_PACKAGES)}},
            )
            subprocess.run(  # noqa: S603 — fixed argv, no shell.
                [sys.executable, "-m", "pip", "install", *_PIP_PACKAGES],
                check=True,
                capture_output=True,
                timeout=_INSTALL_TIMEOUT_S,
            )
            import torch
            from diffusers import AutoPipelineForText2Image

            return AutoPipelineForText2Image, torch

    # ----------------------------------------------------------------- generate
    async def generate(self, prompt: str, *, size: str | None = None) -> ImageResult | str:
        """Generate a PNG under ``media_dir()/image/``. Never raises (B5)."""
        use_size = size or self._default_size
        log.tool.debug(
            "[image.local] generate: entry",
            extra={"_fields": {"prompt_len": len(prompt), "size": use_size}},
        )
        avail = await self.is_available()
        if not avail.available:
            return avail.reason or "local image model unavailable"

        out_dir = StackowlHome.media_dir() / "image"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"image_{uuid4().hex}.png"
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, self._generate_sync, prompt, use_size, out_path)
        except Exception as exc:  # generation failure → structured, never raise (B5).
            log.tool.error(
                "[image.local] generate: generation failed",
                exc_info=exc,
                extra={"_fields": {"prompt_len": len(prompt), "size": use_size}},
            )
            return f"local image generation failed: {type(exc).__name__}: {exc}"

        log.tool.info(
            "[image.local] generate: exit",
            extra={"_fields": {"size": use_size}},
        )
        return ImageResult(
            path=str(out_path),
            size=use_size,
            backend=self.name,
            is_local=True,
        )

    def _generate_sync(self, prompt: str, size: str, out_path: Path) -> None:
        """Synchronous generation — called via run_in_executor."""
        log.tool.debug(
            "[image.local] _generate_sync: entry",
            extra={"_fields": {"prompt_len": len(prompt)}},
        )
        width, height = self._parse_size(size)
        result = self._pipeline(prompt=prompt, width=width, height=height)
        result.images[0].save(str(out_path))
        log.tool.debug("[image.local] _generate_sync: exit")

    @staticmethod
    def _parse_size(size: str) -> tuple[int, int]:
        """Parse a 'WIDTHxHEIGHT' string into a (width, height) int tuple."""
        try:
            w_str, h_str = size.lower().split("x", 1)
            return int(w_str), int(h_str)
        except (ValueError, AttributeError) as exc:
            raise ValueError(
                f"invalid size '{size}' — expected 'WIDTHxHEIGHT' (e.g. '1024x1024')"
            ) from exc
