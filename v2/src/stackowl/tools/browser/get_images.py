"""browser_get_images — enumerate images on the current page.

Runs a small in-page script via ``page.evaluate`` to collect each image's
source, alt text, and natural dimensions, filtering out inline ``data:`` URIs by
default (they carry no fetchable URL and bloat the result). Ported verbatim in
spirit from the reference one-liner; carries no sidecar dependency.

Provenance / port-vs-build: see ``_bmad-output/research/tool-port-analysis.md``
(E2 ``browser_get_images`` row — PORT; the JS snippet is correct + complete).
"""

from __future__ import annotations

import time

from stackowl.infra.observability import log
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.browser.tools import _err, _ok, _services_or_unavailable

_DEFAULT_MAX_IMAGES = 100

# In-page collector. Prefers currentSrc (responsive images); alt defaults to "".
_COLLECT_JS = """
() => Array.from(document.images).map(img => ({
    src: img.currentSrc || img.src || "",
    alt: img.alt || "",
    width: img.naturalWidth || 0,
    height: img.naturalHeight || 0,
}))
"""


class BrowserGetImagesTool(Tool):
    """List images (src, alt, dimensions) on the current page."""

    @property
    def name(self) -> str:
        return "browser_get_images"

    @property
    def description(self) -> str:
        return (
            "List images on the current page as {src, alt, width, height} descriptors. "
            "Inline data: URIs are filtered out by default. Returns {images, count, truncated}."
        )

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "page_handle": {"type": "string"},
                "max_count": {"type": "integer", "default": _DEFAULT_MAX_IMAGES},
                "include_data_uris": {"type": "boolean", "default": False},
            },
            "required": ["session_id"],
        }

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
            action_severity="read",
            toolset_group="browser",
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        t0 = time.monotonic()
        # 1. ENTRY
        session_id = str(kwargs.get("session_id", ""))
        page_handle = kwargs.get("page_handle")
        # Coerce defensively: models often emit numeric params as JSON strings
        # ("5"), which must NOT silently fall back to the default (matches the
        # int|str pattern used by sibling browser tools).
        max_raw = kwargs.get("max_count", _DEFAULT_MAX_IMAGES)
        max_count = _DEFAULT_MAX_IMAGES
        if not isinstance(max_raw, bool) and isinstance(max_raw, int | str):
            try:
                max_count = int(max_raw)
            except (TypeError, ValueError):
                max_count = _DEFAULT_MAX_IMAGES
        include_data = bool(kwargs.get("include_data_uris", False))
        log.tool.info(
            "browser_get_images.execute: entry",
            extra={"_fields": {"session_id": session_id, "max_count": max_count, "include_data": include_data}},
        )
        # Self-healing: no browser substrate / dead session → structured result.
        runtime, sessions, err = _services_or_unavailable()
        if err:
            return _err(err, t0, tool="browser_get_images")
        try:
            _sess, page, _ph = await sessions.get_page(
                session_id, str(page_handle) if page_handle else None
            )
        except Exception as exc:
            return _err(f"browser session unavailable: {type(exc).__name__}: {exc}", t0, tool="browser_get_images")

        # 2. STEP — collect in-page, then filter/cap in Python (keeps the JS minimal).
        try:
            raw = await page.evaluate(_COLLECT_JS)
        except Exception as exc:
            return _err(f"image enumeration failed: {type(exc).__name__}: {exc}", t0, tool="browser_get_images")
        images = []
        for item in raw if isinstance(raw, list) else []:
            src = str(item.get("src", "")) if isinstance(item, dict) else ""
            if not src:
                continue
            if not include_data and src.startswith("data:"):
                continue
            images.append({
                "src": src,
                "alt": str(item.get("alt", "")),
                "width": int(item.get("width", 0) or 0),
                "height": int(item.get("height", 0) or 0),
            })
        truncated = len(images) > max_count
        images = images[:max_count]
        # 3. EXIT
        log.tool.info(
            "browser_get_images.execute: exit",
            extra={"_fields": {"count": len(images), "truncated": truncated}},
        )
        return _ok({"images": images, "count": len(images), "truncated": truncated}, t0, tool="browser_get_images")
