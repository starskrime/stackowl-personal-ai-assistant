"""CloudImageBackend — the OPT-IN cloud image-generation fallback (E10-S4).

DISABLED by default: this backend reports ``is_available()`` False unless a key
REFERENCE is configured (resolved via :class:`SecretResolver` — never an inline
key). is_local=False → the prompt LEAVES the machine, so the tool discloses egress
(mirrors the vision / tts egress precedent).

It targets an OpenAI-compatible ``/images/generations`` endpoint and keeps the
``base_url`` configurable so a SELF-HOSTED image endpoint can be used instead of a
vendor — the self-hosted-first policy still holds (cloud is the fallback, the
endpoint can itself be on-prem). Writes a PNG under ``media_dir()/image/`` and
returns the PATH, never raw bytes. Never raises for an operational failure (B5).
"""

from __future__ import annotations

import asyncio
import base64
import json
import urllib.request
from pathlib import Path
from uuid import uuid4

from stackowl.config.secret_resolver import SecretResolver
from stackowl.infra.observability import log
from stackowl.media.image.base import ImageAvailability, ImageBackend, ImageResult
from stackowl.paths import StackowlHome

__all__ = ["CloudImageBackend"]

_DEFAULT_SIZE = "1024x1024"
_REQUEST_TIMEOUT_S = 180


class CloudImageBackend(ImageBackend):
    """OpenAI-compatible cloud/self-hostable image endpoint. Opt-in, egress-disclosed."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key_ref: str,
        model: str = "dall-e-3",
        default_size: str = _DEFAULT_SIZE,
    ) -> None:
        self._base_url = (base_url or "").rstrip("/")
        self._api_key_ref = api_key_ref or ""
        self._model = model or "dall-e-3"
        self._default_size = default_size or _DEFAULT_SIZE
        log.tool.debug(
            "[image.cloud] init",
            extra={"_fields": {"has_key_ref": bool(self._api_key_ref)}},
        )

    @property
    def name(self) -> str:
        return "cloud"

    @property
    def is_local(self) -> bool:
        return False

    async def is_available(self) -> ImageAvailability:
        """Available ONLY when a key reference is configured. Never raises (B5)."""
        if not self._api_key_ref:
            return ImageAvailability.no("cloud image generation is disabled (no API key configured)")
        if not self._base_url:
            return ImageAvailability.no("cloud image generation has no base_url configured")
        try:
            SecretResolver.resolve(self._api_key_ref)
        except Exception as exc:  # bad reference → unavailable, never raise.
            log.tool.warning(
                "[image.cloud] is_available: key reference did not resolve",
                extra={"_fields": {"error": type(exc).__name__}},
            )
            return ImageAvailability.no(
                f"cloud image key reference did not resolve ({type(exc).__name__})"
            )
        return ImageAvailability.ok()

    async def generate(self, prompt: str, *, size: str | None = None) -> ImageResult | str:
        """POST to the image endpoint → write the PNG. Never raises (B5)."""
        use_size = size or self._default_size
        log.tool.debug(
            "[image.cloud] generate: entry",
            extra={"_fields": {"prompt_len": len(prompt), "size": use_size}},
        )
        avail = await self.is_available()
        if not avail.available:
            return avail.reason or "cloud image generation unavailable"
        try:
            api_key = SecretResolver.resolve(self._api_key_ref)
        except Exception as exc:
            return f"cloud image key reference did not resolve: {type(exc).__name__}"

        out_dir = StackowlHome.media_dir() / "image"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"image_{uuid4().hex}.png"
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, self._post_sync, prompt, use_size, api_key, out_path)
        except Exception as exc:  # endpoint failure → structured, never raise (B5).
            log.tool.error(
                "[image.cloud] generate: request failed",
                exc_info=exc,
                extra={"_fields": {"prompt_len": len(prompt), "size": use_size}},
            )
            return f"cloud image request failed: {type(exc).__name__}: {exc}"

        log.tool.info(
            "[image.cloud] generate: exit",
            extra={"_fields": {"size": use_size}},
        )
        return ImageResult(
            path=str(out_path),
            size=use_size,
            backend=self.name,
            is_local=False,
        )

    def _post_sync(self, prompt: str, size: str, api_key: str, out_path: Path) -> None:
        """Synchronous HTTP POST — called via run_in_executor."""
        url = f"{self._base_url}/images/generations"
        body = json.dumps(
            {"model": self._model, "prompt": prompt, "size": size, "response_format": "b64_json"}
        ).encode("utf-8")
        req = urllib.request.Request(  # noqa: S310 — https endpoint, configured base_url.
            url,
            data=body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        # Log the URL PATH only (strip any query) — never the key or the prompt.
        log.tool.debug(
            "[image.cloud] _post_sync: request",
            extra={"_fields": {"endpoint": "/images/generations"}},
        )
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT_S) as resp:  # noqa: S310
            payload = json.loads(resp.read().decode("utf-8"))
        b64 = payload["data"][0]["b64_json"]
        out_path.write_bytes(base64.b64decode(b64))
