"""browser_console — read buffered console messages + page errors.

Returns the page's captured console output split into ``messages`` and
``errors`` — the LLM-friendly shape that keeps log noise separate from uncaught
exceptions. Buffers are filled eagerly by ``BrowserSession`` from page birth (via
``page.on("console")`` / ``page.on("pageerror")``), so output emitted before this
tool is first called is still here. The buffer is a bounded per-page ring (oldest
dropped) so a noisy page cannot grow it without limit.

Provenance / port-vs-build: see ``_bmad-output/research/tool-port-analysis.md``
(E2 ``browser_console`` row — PORT of the messages/errors split shape; wired to
the in-process engine's events, not a sidecar). The ``expression`` mode is dropped
(``browser_eval_js`` covers it).
"""

from __future__ import annotations

import time

from stackowl.infra.observability import log
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.browser.tools import _err, _ok, _services_or_unavailable


class BrowserConsoleTool(Tool):
    """Return buffered console messages and page errors for the active page."""

    @property
    def name(self) -> str:
        return "browser_console"

    @property
    def description(self) -> str:
        return (
            "Read the active page's buffered console output as {messages, errors}. "
            "messages carry {type, text} (type is log/warning/error/…); errors carry "
            "uncaught page exceptions. Set clear=true to empty the buffer after reading."
        )

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "page_handle": {"type": "string"},
                "clear": {"type": "boolean", "default": False, "description": "Empty the buffer after reading."},
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
        clear = bool(kwargs.get("clear", False))
        log.tool.info(
            "browser_console.execute: entry",
            extra={"_fields": {"session_id": session_id, "clear": clear}},
        )
        # Self-healing: no browser substrate / dead session → structured result.
        runtime, sessions, err = _services_or_unavailable()
        if err:
            return _err(err, t0, tool="browser_console")
        try:
            sess, _page, ph = await sessions.get_page(
                session_id, str(page_handle) if page_handle else None
            )
        except Exception as exc:
            return _err(f"browser session unavailable: {type(exc).__name__}: {exc}", t0, tool="browser_console")

        # 2. STEP — snapshot the bounded per-page buffer (may be absent → empty).
        obs = sess.observers.get(ph)
        messages = list(obs.console) if obs is not None else []
        errors = list(obs.errors) if obs is not None else []
        if clear and obs is not None:
            obs.console.clear()
            obs.errors.clear()
        # 3. EXIT
        log.tool.info(
            "browser_console.execute: exit",
            extra={"_fields": {"messages": len(messages), "errors": len(errors), "cleared": clear}},
        )
        return _ok(
            {
                "messages": messages,
                "errors": errors,
                "message_count": len(messages),
                "error_count": len(errors),
            },
            t0,
            tool="browser_console",
        )
