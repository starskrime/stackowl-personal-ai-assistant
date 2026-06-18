"""browser_press — press a key or chord on the active page.

A thin wrapper over the in-process engine's ``page.keyboard.press(key)``, which
dispatches to the focused element and supports native modifier chords
(``Control+A``, ``Shift+Tab``). Key-name semantics are validated by the engine;
we reject only structurally-malformed input (empty / oversized / control chars)
before dispatch, then surface an engine rejection as a structured error.

Provenance / port-vs-build: see ``_bmad-output/research/tool-port-analysis.md``
(E2 ``browser_press`` row — BUILD; one in-process call with native chord support).
"""

from __future__ import annotations

import time

from stackowl.infra.observability import log
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.browser.tools import _err, _ok, _services_or_unavailable

_MAX_KEY_LEN = 64


def _is_structurally_valid_key(key: str) -> bool:
    """Reject empty / oversized / control-char keys before reaching the engine.

    Deliberately does NOT enumerate key names (engine-/protocol-defined tokens,
    not natural language) — the engine is the authority on which names are valid.
    """
    if not key or len(key) > _MAX_KEY_LEN:
        return False
    return all(ord(ch) >= 0x20 for ch in key)  # no control characters


class BrowserPressTool(Tool):
    """Press a key or modifier chord on the active page."""

    @property
    def name(self) -> str:
        return "browser_press"

    @property
    def description(self) -> str:
        return (
            "Press a key (or chord) on the active page, dispatched to the focused element. "
            "Examples: 'Enter', 'Tab', 'Escape', 'Control+A', 'Shift+Tab', 'ArrowDown'. "
            "Returns {ok, key}."
        )

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "page_handle": {"type": "string"},
                "key": {"type": "string", "description": "Key or chord, e.g. 'Enter' or 'Control+A'."},
            },
            "required": ["session_id", "key"],
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
        key = str(kwargs.get("key", ""))
        log.tool.info(
            "browser_press.execute: entry",
            extra={"_fields": {"session_id": session_id, "key": key[:_MAX_KEY_LEN]}},
        )
        # 2. DECISION — structural validation before any engine round-trip.
        if not _is_structurally_valid_key(key):
            return _err(f"Invalid key: {key[:_MAX_KEY_LEN]!r}", t0, tool="browser_press")
        # Self-healing: no browser substrate / dead session → structured result.
        runtime, sessions, err = _services_or_unavailable()
        if err:
            return _err(err, t0, tool="browser_press")
        try:
            _sess, page, _ph = await sessions.get_page(
                session_id, str(page_handle) if page_handle else None
            )
        except Exception as exc:
            return _err(f"browser session unavailable: {type(exc).__name__}: {exc}", t0, tool="browser_press")

        # 3. STEP — dispatch the keypress (engine validates the key name).
        try:
            await page.keyboard.press(key)
        except Exception as exc:
            return _err(f"press failed: {type(exc).__name__}: {exc}", t0, tool="browser_press")
        # 4. EXIT
        log.tool.info("browser_press.execute: exit", extra={"_fields": {"ok": True}})
        return _ok({"ok": True, "key": key}, t0, tool="browser_press")
