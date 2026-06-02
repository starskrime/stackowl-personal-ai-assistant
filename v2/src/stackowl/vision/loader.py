"""ImageLoader — resolve an image input to validated bytes + MIME type (E10-S1).

Two input shapes:

* a LOCAL path — confined to the workspace via the shared ``path_guard``
  (``is_within_root``), the same primitive every file-touching tool uses;
* an ``http(s)`` URL — downloaded through the EXISTING shared :class:`SsrfGuard`
  (resolve-then-validate; a loopback/private/link-local target is REFUSED),
  ``follow_redirects=False``, streamed under a hard SIZE CAP, with the resulting
  MIME validated as ``image/*``.

Self-healing / no-hidden-errors (B5): every failure path returns a structured
:class:`LoadError` — the loader NEVER raises, so the S2 tool can surface a clean
message instead of a stack trace. Sensitive-data: only the image SIZE + MIME and
the URL origin+path (never the query string, never the bytes) are logged.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from stackowl.infra.net.ssrf_guard import SsrfGuard
from stackowl.infra.observability import log
from stackowl.tools.io.path_guard import is_within_root, resolve_in_workspace

__all__ = ["ImageLoader", "LoadError", "LoadedImage"]

# Hard cap on a downloaded/loaded image (party Security §): reject anything bigger
# so a malicious/huge URL cannot exhaust memory. 10 MiB is generous for a photo.
_MAX_IMAGE_BYTES = 10 * 1024 * 1024
_DOWNLOAD_TIMEOUT_S = 30.0

# Map a file extension / sniffed signature to an image MIME. Used to (a) derive a
# MIME for a local file and (b) sanity-check a URL whose content-type lies/omits.
_EXT_MIME: dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}

# Magic-byte signatures → MIME (network-free sniff; no Pillow dependency).
_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"BM", "image/bmp"),
)


@dataclass(frozen=True)
class LoadedImage:
    """Successfully resolved image: raw bytes + the validated image MIME type."""

    data: bytes
    media_type: str

    @property
    def size(self) -> int:
        return len(self.data)


@dataclass(frozen=True)
class LoadError:
    """Structured failure — the loader returns this instead of raising."""

    reason: str


def _sniff_mime(data: bytes) -> str | None:
    """Return an image MIME from magic bytes, or None if unrecognized.

    WEBP is RIFF-container: ``RIFF....WEBP``.
    """
    for sig, mime in _MAGIC:
        if data.startswith(sig):
            return mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


class ImageLoader:
    """Resolves a local path or http(s) URL to validated image bytes.

    ``ssrf_guard`` is injectable so tests can supply a deterministic resolver;
    the default is the shared production guard (fails closed on private targets).
    """

    def __init__(self, *, ssrf_guard: SsrfGuard | None = None) -> None:
        self._ssrf = ssrf_guard or SsrfGuard()

    async def load(self, source: str) -> LoadedImage | LoadError:
        """Resolve ``source`` (path or http(s) URL) to a LoadedImage or LoadError."""
        # 1. ENTRY
        log.tool.debug(
            "[image_loader] load: entry", extra={"_fields": {"source_len": len(source)}}
        )
        if not source or not source.strip():
            return LoadError("empty image source")
        scheme = (urlsplit(source).scheme or "").lower()
        # 2. DECISION — URL vs local path.
        if scheme in ("http", "https"):
            return await self._load_url(source)
        if scheme:
            # file://, ftp://, data:, etc. — not supported (avoid scheme surprises).
            return LoadError(f"unsupported image source scheme '{scheme}'")
        return self._load_path(source)

    # ----------------------------------------------------------------- local
    def _load_path(self, path_str: str) -> LoadedImage | LoadError:
        """Read a workspace-confined local image file."""
        target = resolve_in_workspace(path_str)
        if not is_within_root(target):
            log.tool.warning(
                "[image_loader] load: path traversal denied",
                extra={"_fields": {"path": path_str}},
            )
            return LoadError("path is outside the workspace")
        try:
            if not target.is_file():
                return LoadError(f"file not found: {path_str}")
            size = target.stat().st_size
            if size > _MAX_IMAGE_BYTES:
                return LoadError(
                    f"image too large: {size} bytes exceeds the {_MAX_IMAGE_BYTES}-byte cap"
                )
            data = target.read_bytes()
        except OSError as exc:
            log.tool.error(
                "[image_loader] load: read failed",
                exc_info=exc,
                extra={"_fields": {"path": path_str}},
            )
            return LoadError(f"cannot read image file: {exc}")
        mime = self._resolve_mime(data, target)
        if mime is None:
            return LoadError("file is not a recognized image format")
        log.tool.debug(
            "[image_loader] load: exit (local)",
            extra={"_fields": {"size": len(data), "mime": mime}},
        )
        return LoadedImage(data=data, media_type=mime)

    def _resolve_mime(self, data: bytes, target: Path) -> str | None:
        """Derive an image MIME from magic bytes, falling back to the extension."""
        sniffed = _sniff_mime(data)
        if sniffed is not None:
            return sniffed
        ext = target.suffix.lower()
        return _EXT_MIME.get(ext)

    # ------------------------------------------------------------------- URL
    async def _load_url(self, url: str) -> LoadedImage | LoadError:
        """Download an image over http(s) behind the shared SSRF guard."""
        # SSRF egress policy: refuse a private/loopback/link-local target BEFORE
        # any socket is opened (resolve-then-validate, fails closed).
        allowed, reason = self._ssrf.is_allowed(url)
        if not allowed:
            log.tool.warning(
                "[image_loader] load: SSRF blocked",
                extra={"_fields": {"reason": reason, "egress": self._egress(url)}},
            )
            return LoadError(f"image URL refused by egress policy: {reason}")
        try:
            # follow_redirects=False: a redirect could re-point at an internal host
            # AFTER the guard ran, so a 3xx is rejected rather than chased (SSRF).
            async with httpx.AsyncClient(
                timeout=_DOWNLOAD_TIMEOUT_S, follow_redirects=False
            ) as client:
                log.tool.debug(
                    "[image_loader] load: GET",
                    extra={"_fields": {"egress": self._egress(url)}},
                )
                async with client.stream("GET", url) as resp:
                    resp.raise_for_status()
                    body = b""
                    async for chunk in resp.aiter_bytes():
                        body += chunk
                        if len(body) > _MAX_IMAGE_BYTES:
                            return LoadError(
                                f"image too large: exceeds the {_MAX_IMAGE_BYTES}-byte cap"
                            )
                    header_mime = (resp.headers.get("content-type") or "").split(";")[0].strip()
        except httpx.HTTPStatusError as exc:
            return LoadError(f"image URL returned HTTP {exc.response.status_code}")
        except httpx.HTTPError as exc:
            log.tool.error(
                "[image_loader] load: download failed",
                exc_info=exc,
                extra={"_fields": {"egress": self._egress(url)}},
            )
            return LoadError(f"image download failed: {exc!r}")
        except Exception as exc:  # B5 — never raise out of the loader.
            log.tool.error(
                "[image_loader] load: unexpected download error",
                exc_info=exc,
                extra={"_fields": {"egress": self._egress(url)}},
            )
            return LoadError(f"image download failed: {type(exc).__name__}")

        # Validate the payload is genuinely an image: magic bytes win; fall back to
        # a declared image/* content-type. A non-image is REFUSED (never forwarded).
        mime = _sniff_mime(body) or (header_mime if header_mime.startswith("image/") else None)
        if mime is None:
            return LoadError(
                f"downloaded content is not an image (content-type={header_mime or 'unknown'})"
            )
        log.tool.debug(
            "[image_loader] load: exit (url)",
            extra={"_fields": {"size": len(body), "mime": mime}},
        )
        return LoadedImage(data=body, media_type=mime)

    @staticmethod
    def _egress(url: str) -> str:
        """Origin+path only (strip the query, which may carry a key) for logging."""
        try:
            parts = urlsplit(url)
            return f"{parts.scheme}://{parts.netloc}{parts.path}"
        except Exception:  # pragma: no cover — defensive
            return "(unparseable url)"
