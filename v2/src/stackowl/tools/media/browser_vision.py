"""browser_vision — screenshot the current browser page + analyze it (E10-S5).

A THIN composition of two committed pieces:

* the E2 screenshot primitive — ``sessions.get_page(session_id, page_handle)``
  resolves the active :class:`BrowserSession` page, then ``page.screenshot()``
  writes a PNG under ``runtime.settings.screenshots_dir`` (same mechanism as
  ``browser_screenshot``; NOT reimplemented here);
* the E10-S1 vision substrate via the shared :func:`analyze_image_bytes` core
  (select a vision provider LOCAL-FIRST → DocumentBlock → ``provider.complete`` →
  egress-disclose on a cloud backend) — the SAME analysis ``vision_analyze`` runs.

The screenshot lives under ``screenshots_dir`` (the home root, OUTSIDE the
workspace ``data_root``), so it is NOT routed through the workspace-confined
``ImageLoader``; instead the trusted bytes we just captured in-process are fed
straight to the shared analyzer. The screenshot PATH is surfaced in the result so
the agent can reference / deliver it.

Self-healing / no-hidden-errors (B5) — every leg degrades to a STRUCTURED result,
NEVER raises:

* no browser runtime / no active page → "no browser page to analyze — open a page
  first" (NO vision call);
* the screenshot capture fails → structured (NO vision call);
* no vision provider → the actionable "install a local vision model" message;
* ``provider.complete`` raises → structured.

Severity ``read`` (it reads the page + analyzes; the only host side-effect is the
screenshot file under ``screenshots_dir``). ``toolset_group`` ``"media"``.
Sensitive-data (B5): only the screenshot PATH + SIZE + backend NAME are logged —
never image bytes; the question is logged by LENGTH only.
"""

from __future__ import annotations

import contextlib
import json
import time
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from stackowl.infra.observability import log
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.browser.tools import (
    _BROWSER_ERRORS,
    _services_or_unavailable,
)
from stackowl.vision.analyzer import analyze_image_bytes

_TOOLSET_GROUP = "media"
_DEFAULT_QUESTION = "Describe what is visible on this page."
_NO_PAGE_MSG = "no browser page to analyze — open a page first (browser_open / browser_navigate)"
# Bound the screenshot bytes sent to the vision model (parity with ImageLoader's
# cap) — a full_page capture of a very tall page can balloon; cap it to avoid an
# unbounded memory/cost/provider-413 send.
_MAX_SCREENSHOT_BYTES = 10 * 1024 * 1024


class BrowserVisionArgs(BaseModel):
    """Validated arguments for one ``browser_vision`` invocation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    session_id: str
    page_handle: str | None = None
    question: str = _DEFAULT_QUESTION
    full_page: bool = False


class BrowserVisionTool(Tool):
    """Screenshot the current browser page and analyze it with a vision model.

    Local-first: the screenshot stays on the box when a local vision model is
    configured; a cloud backend is disclosed in the output (the image left the
    machine). Read-only beyond the screenshot file; degrades to a structured
    result, never raises.
    """

    @property
    def name(self) -> str:
        return "browser_vision"

    @property
    def description(self) -> str:
        return (
            "Screenshot the CURRENT browser page and analyze what is visible with a "
            "vision model (e.g. 'is there a login form?', 'summarize this page'). "
            "Requires an open browser session/page (use browser_open/browser_navigate "
            "first). Runs on a LOCAL vision model when one is configured (the "
            "screenshot stays on this machine); a cloud fallback is disclosed. Returns "
            "the description plus the screenshot file path. Read-only."
        )

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "The browser session to screenshot (from browser_open).",
                },
                "page_handle": {
                    "type": "string",
                    "description": "Optional page handle within the session; defaults to a new page.",
                },
                "question": {
                    "type": "string",
                    "description": "What to ask about the page. Defaults to a full description.",
                    "default": _DEFAULT_QUESTION,
                },
                "full_page": {
                    "type": "boolean",
                    "description": "Capture the full scrollable page rather than the viewport.",
                    "default": False,
                },
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
            toolset_group=_TOOLSET_GROUP,
        )

    # --------------------------------------------------------------- execute
    async def execute(self, **kwargs: object) -> ToolResult:
        # 1. ENTRY — log the question LENGTH only (never image bytes).
        t0 = time.monotonic()
        try:
            args = BrowserVisionArgs(**kwargs)  # type: ignore[arg-type]
        except ValidationError as exc:
            log.tool.warning(
                "browser_vision.execute: invalid args",
                extra={"_fields": {"errors": exc.error_count()}},
            )
            return self._err(f"invalid arguments — {exc.error_count()} error(s)", t0)
        log.tool.info(
            "browser_vision.execute: entry",
            extra={"_fields": {
                "session_id": args.session_id,
                "full_page": args.full_page,
                "question_len": len(args.question),
            }},
        )
        if not args.session_id or not args.session_id.strip():
            return self._err(_NO_PAGE_MSG, t0)

        # 2. STEP — capture a screenshot via the E2 mechanism (no reimplementation).
        capture = await self._capture(args)
        if isinstance(capture, str):  # an error message — no browser page / capture failed
            log.tool.info(
                "browser_vision.execute: capture failed — structured result",
                extra={"_fields": {"reason": capture}},
            )
            return self._err(capture, t0)
        out_path, data = capture
        log.tool.debug(
            "browser_vision.execute: screenshot captured",
            extra={"_fields": {"path": str(out_path), "size": len(data)}},
        )

        # 3-5. Analyze the trusted screenshot bytes on the SHARED vision core
        # (select→DocumentBlock→complete→egress-disclose). The screenshot lives
        # OUTSIDE the workspace, so we feed the captured bytes directly (the
        # workspace-confined ImageLoader would reject the path).
        from stackowl.pipeline.services import get_services

        analysis = await analyze_image_bytes(
            get_services().provider_registry,
            data=data,
            media_type="image/png",
            question=args.question,
        )
        if not analysis.success:
            return self._err(analysis.error or "vision analysis failed", t0, screenshot_path=str(out_path))
        return self._ok(
            analysis.description,
            t0,
            backend=analysis.backend or "",
            local=analysis.is_local,
            screenshot_path=str(out_path),
        )

    # ---------------------------------------------------------------- capture
    async def _capture(self, args: BrowserVisionArgs) -> tuple[Path, bytes] | str:
        """Screenshot the active page via the E2 mechanism → (path, bytes) | error.

        Reuses ``sessions.get_page`` + ``page.screenshot`` + ``screenshots_dir``
        exactly as ``browser_screenshot`` does. Returns an error STRING (not a
        raise) on every failure leg so the caller surfaces a structured result.
        """
        runtime, sessions, err = _services_or_unavailable()
        if err is not None or runtime is None or sessions is None:
            return _NO_PAGE_MSG
        try:
            _sess, page, _ph = await sessions.get_page(
                args.session_id, args.page_handle
            )
            out_dir: Path = runtime.settings.screenshots_dir
            out_dir.mkdir(parents=True, exist_ok=True)
            ts = int(time.time() * 1000)
            out_path = out_dir / f"{args.session_id[:8]}-{ts}-vision.png"
            await page.screenshot(path=str(out_path), full_page=args.full_page)
        except _BROWSER_ERRORS as exc:
            log.tool.error(
                "browser_vision.execute: screenshot failed — structured failure",
                exc_info=exc,
                extra={"_fields": {"error_type": type(exc).__name__}},
            )
            return (
                f"could not screenshot the page (it may have navigated/closed, or there "
                f"is no open page): {type(exc).__name__}: {exc}"
            )
        with contextlib.suppress(OSError):
            out_path.chmod(0o600)
        try:
            data = out_path.read_bytes()
        except OSError as exc:
            log.tool.error(
                "browser_vision.execute: reading screenshot back failed",
                exc_info=exc,
                extra={"_fields": {"path": str(out_path)}},
            )
            return f"screenshot written but could not be read back: {exc}"
        if len(data) > _MAX_SCREENSHOT_BYTES:
            log.tool.warning(
                "browser_vision.execute: screenshot too large — refusing to send",
                extra={"_fields": {"size": len(data), "cap": _MAX_SCREENSHOT_BYTES}},
            )
            return (
                f"the page screenshot is too large to analyze ({len(data)} bytes > "
                f"{_MAX_SCREENSHOT_BYTES} cap); try again with full_page disabled."
            )
        return out_path, data

    # ---------------------------------------------------------------- helpers
    def _ok(
        self,
        description: str,
        t0: float,
        *,
        backend: str,
        local: bool,
        screenshot_path: str,
    ) -> ToolResult:
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.info(
            "browser_vision.execute: exit",
            extra={"_fields": {
                "success": True, "backend": backend, "local": local,
                "screenshot_path": screenshot_path, "output_len": len(description),
                "duration_ms": duration_ms,
            }},
        )
        # The screenshot PATH is surfaced (so the agent can reference/deliver it)
        # alongside the description, backend, and locality flag.
        payload = {
            "description": description,
            "backend": backend,
            "screenshot_path": screenshot_path,
            "local": local,
        }
        return ToolResult(
            success=True, output=json.dumps(payload, default=str), error=None, duration_ms=duration_ms
        )

    def _err(self, msg: str, t0: float, *, screenshot_path: str | None = None) -> ToolResult:
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.info(
            "browser_vision.execute: exit",
            extra={"_fields": {
                "success": False, "error": msg,
                "screenshot_path": screenshot_path, "duration_ms": duration_ms,
            }},
        )
        return ToolResult(success=False, output="", error=msg, duration_ms=duration_ms)
