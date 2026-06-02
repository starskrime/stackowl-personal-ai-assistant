"""CloudTtsBackend — the OPT-IN cloud text-to-speech fallback (E10-S3).

DISABLED by default: this backend reports ``is_available()`` False unless a key
REFERENCE is configured (resolved via :class:`SecretResolver` — never an inline
key). is_local=False → the text LEAVES the machine, so the tool discloses egress
(mirrors the pdf Mode B / vision egress precedent).

It targets an OpenAI-compatible ``/audio/speech`` endpoint and keeps the
``base_url`` configurable so a SELF-HOSTED speech endpoint can be used instead of
a vendor — the self-hosted-first policy still holds (cloud is the fallback, the
endpoint can itself be on-prem). Writes a file under ``media_dir()/tts/`` and
returns the PATH, never raw bytes. Never raises for an operational failure (B5).
"""

from __future__ import annotations

import asyncio
import time
import urllib.request
from pathlib import Path
from uuid import uuid4

from stackowl.config.secret_resolver import SecretResolver
from stackowl.infra.observability import log
from stackowl.media.tts.base import TtsAvailability, TtsBackend, TtsResult
from stackowl.paths import StackowlHome

__all__ = ["CloudTtsBackend"]

_DEFAULT_VOICE = "alloy"
_REQUEST_TIMEOUT_S = 120


class CloudTtsBackend(TtsBackend):
    """OpenAI-compatible cloud/self-hostable speech endpoint. Opt-in, egress-disclosed."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key_ref: str,
        voice: str = _DEFAULT_VOICE,
        model: str = "tts-1",
    ) -> None:
        self._base_url = (base_url or "").rstrip("/")
        self._api_key_ref = api_key_ref or ""
        self._voice = voice or _DEFAULT_VOICE
        self._model = model or "tts-1"
        log.tool.debug(
            "[tts.cloud] init",
            extra={"_fields": {"has_key_ref": bool(self._api_key_ref)}},
        )

    @property
    def name(self) -> str:
        return "cloud"

    @property
    def is_local(self) -> bool:
        return False

    async def is_available(self, voice: str | None = None) -> TtsAvailability:
        """Available ONLY when a key reference is configured. Never raises (B5)."""
        if not self._api_key_ref:
            return TtsAvailability.no("cloud TTS is disabled (no API key configured)")
        if not self._base_url:
            return TtsAvailability.no("cloud TTS has no base_url configured")
        try:
            SecretResolver.resolve(self._api_key_ref)
        except Exception as exc:  # bad reference → unavailable, never raise.
            log.tool.warning(
                "[tts.cloud] is_available: key reference did not resolve",
                extra={"_fields": {"error": type(exc).__name__}},
            )
            return TtsAvailability.no(
                f"cloud TTS key reference did not resolve ({type(exc).__name__})"
            )
        return TtsAvailability.ok()

    async def synthesize(self, text: str, *, voice: str | None) -> TtsResult | str:
        """POST to the speech endpoint → write the audio file. Never raises (B5)."""
        t0 = time.monotonic()
        use_voice = voice or self._voice
        log.tool.debug(
            "[tts.cloud] synthesize: entry",
            extra={"_fields": {"text_len": len(text), "voice": use_voice}},
        )
        avail = await self.is_available()
        if not avail.available:
            return avail.reason or "cloud TTS unavailable"
        try:
            api_key = SecretResolver.resolve(self._api_key_ref)
        except Exception as exc:
            return f"cloud TTS key reference did not resolve: {type(exc).__name__}"

        out_dir = StackowlHome.media_dir() / "tts"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"tts_{uuid4().hex}.mp3"
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(
                None, self._post_sync, text, use_voice, api_key, out_path
            )
        except Exception as exc:  # endpoint failure → structured, never raise (B5).
            log.tool.error(
                "[tts.cloud] synthesize: request failed",
                exc_info=exc,
                extra={"_fields": {"text_len": len(text), "voice": use_voice}},
            )
            return f"cloud TTS request failed: {type(exc).__name__}: {exc}"

        elapsed_ms = (time.monotonic() - t0) * 1000
        log.tool.info(
            "[tts.cloud] synthesize: exit",
            extra={"_fields": {"voice": use_voice, "wall_ms": elapsed_ms}},
        )
        return TtsResult(
            path=str(out_path),
            duration_ms=elapsed_ms,
            voice=use_voice,
            backend=self.name,
            is_local=False,
        )

    def _post_sync(self, text: str, voice: str, api_key: str, out_path: Path) -> None:
        """Synchronous HTTP POST — called via run_in_executor."""
        import json

        url = f"{self._base_url}/audio/speech"
        body = json.dumps(
            {"model": self._model, "voice": voice, "input": text}
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
        # Log the URL PATH only (strip any query) — never the key or the text.
        log.tool.debug(
            "[tts.cloud] _post_sync: request",
            extra={"_fields": {"endpoint": "/audio/speech"}},
        )
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT_S) as resp:  # noqa: S310
            audio = resp.read()
        out_path.write_bytes(audio)
