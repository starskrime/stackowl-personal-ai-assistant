"""ReadFileTool — reads a file, with path-traversal guard."""

from __future__ import annotations

import time

from stackowl.infra.observability import log
from stackowl.tools.base import Tool, ToolResult
from stackowl.tools.io.path_guard import is_within_root as _guard  # shared guard (E3)
from stackowl.tools.io.path_guard import resolve_in_workspace as _resolve  # workspace anchoring


class ReadFileTool(Tool):
    """Read a file from within STACKOWL_DATA_DIR."""

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return "Read the contents of a file. Path must be inside STACKOWL_DATA_DIR."

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Relative or absolute file path"}},
            "required": ["path"],
        }

    async def execute(self, **kwargs: object) -> ToolResult:
        path_str = str(kwargs.get("path", ""))
        log.tool.debug("read_file.execute: entry", extra={"_fields": {"path": path_str}})
        t0 = time.monotonic()
        # A relative path anchors UNDER the workspace (mirrors search_files hit
        # paths), so a relative hit piped straight in round-trips. Guard confines.
        target = _resolve(path_str)
        if not _guard(target):
            duration_ms = (time.monotonic() - t0) * 1000
            log.tool.warning(
                "read_file.execute: path traversal denied",
                extra={"_fields": {"path": path_str}},
            )
            return ToolResult(success=False, output="", error="Path traversal denied", duration_ms=duration_ms)
        try:
            content = target.read_text(encoding="utf-8")
            duration_ms = (time.monotonic() - t0) * 1000
            log.tool.debug(
                "read_file.execute: exit",
                extra={"_fields": {"path": path_str, "bytes": len(content), "duration_ms": duration_ms}},
            )
            return ToolResult(success=True, output=content, duration_ms=duration_ms)
        except FileNotFoundError:
            duration_ms = (time.monotonic() - t0) * 1000
            return ToolResult(success=False, output="", error=f"File not found: {path_str}", duration_ms=duration_ms)
        except OSError as exc:
            duration_ms = (time.monotonic() - t0) * 1000
            log.tool.error(
                "read_file.execute: OS error",
                exc_info=exc,
                extra={"_fields": {"path": path_str}},
            )
            return ToolResult(success=False, output="", error=str(exc), duration_ms=duration_ms)
