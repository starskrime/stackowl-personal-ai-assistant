"""browser_back — navigate the active page back one history entry.

A thin wrapper over the in-process browser engine's ``page.go_back()``. Mirrors
``BrowserNavigateTool``'s result shape. When there is no previous entry the
engine returns no response; we surface that as a structured no-op rather than an
error (self-healing — the model should not treat "nothing to go back to" as a
failure).

Provenance / port-vs-build: see ``_bmad-output/research/tool-port-analysis.md``
(E2 ``browser_back`` row — BUILD; the real semantics are one in-process call, not
the reference impl's sidecar round-trip).
"""

from __future__ import annotations

import time

from stackowl.infra.observability import log
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.browser._logging import url_path_only
from stackowl.tools.browser.tools import _err, _ok, _services_or_unavailable

_DEFAULT_NAV_TIMEOUT_MS = 30_000
_WAIT_STATES = ("domcontentloaded", "load", "networkidle")


class BrowserBackTool(Tool):
    """Go back one entry in the active page's history."""

    @property
    def name(self) -> str:
        return "browser_back"

    @property
    def description(self) -> str:
        return (
            "Navigate the active page back one entry in history. Returns "
            "{navigated, final_url, title, status}; navigated=false when there is no "
            "previous page (a no-op, not an error)."
        )

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "page_handle": {"type": "string"},
                "wait_for": {
                    "type": "string",
                    "enum": list(_WAIT_STATES),
                    "default": "domcontentloaded",
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
            toolset_group="browser",
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        t0 = time.monotonic()
        # 1. ENTRY
        session_id = str(kwargs.get("session_id", ""))
        page_handle = kwargs.get("page_handle")
        wait_for = str(kwargs.get("wait_for", "domcontentloaded"))
        if wait_for not in _WAIT_STATES:
            wait_for = "domcontentloaded"
        log.tool.info(
            "browser_back.execute: entry",
            extra={"_fields": {"session_id": session_id, "wait_for": wait_for}},
        )
        # Self-healing: no browser substrate / dead session → structured result.
        runtime, sessions, err = _services_or_unavailable()
        if err:
            return _err(err, t0, tool="browser_back")
        try:
            _sess, page, _ph = await sessions.get_page(
                session_id, str(page_handle) if page_handle else None
            )
        except Exception as exc:
            return _err(f"browser session unavailable: {type(exc).__name__}: {exc}", t0, tool="browser_back")

        # 2. DECISION / 3. STEP — go_back returns None when history is empty.
        try:
            resp = await page.go_back(wait_until=wait_for, timeout=_DEFAULT_NAV_TIMEOUT_MS)
        except Exception as exc:
            return _err(f"go_back failed: {type(exc).__name__}: {exc}", t0, tool="browser_back")
        if resp is None:
            log.tool.info("browser_back.execute: exit — no previous page", extra={"_fields": {"navigated": False}})
            return _ok({"navigated": False, "reason": "no previous page in history"}, t0, tool="browser_back")

        await runtime.record_navigation()
        status = resp.status if resp is not None else 0
        title = await page.title()
        # 4. EXIT
        log.tool.info(
            "browser_back.execute: exit",
            extra={"_fields": {"navigated": True, "status": status}},
        )
        return _ok(
            {
                "navigated": True,
                "final_url": url_path_only(page.url),
                "title": title,
                "status": status,
            },
            t0,
            tool="browser_back",
        )
