"""WriteFileTool — writes a file, with path-traversal guard and parent dir creation."""

from __future__ import annotations

import time
from pathlib import Path

from stackowl.infra.observability import log
from stackowl.tools.base import Tool, ToolResult
from stackowl.tools.io.read_file import _guard


class WriteFileTool(Tool):
    """Write content to a file inside STACKOWL_DATA_DIR."""

    @property
    def name(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return "Write content to a file. Path must be inside STACKOWL_DATA_DIR."

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative or absolute file path"},
                "content": {"type": "string", "description": "Text content to write"},
            },
            "required": ["path", "content"],
        }

    async def execute(self, **kwargs: object) -> ToolResult:
        path_str = str(kwargs.get("path", ""))
        content = str(kwargs.get("content", ""))
        log.tool.debug("write_file.execute: entry", extra={"_fields": {"path": path_str, "content_len": len(content)}})
        t0 = time.monotonic()
        target = Path(path_str)
        if not _guard(target):
            duration_ms = (time.monotonic() - t0) * 1000
            log.tool.warning("write_file.execute: path traversal denied", extra={"_fields": {"path": path_str}})
            return ToolResult(success=False, output="", error="Path traversal denied", duration_ms=duration_ms)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            duration_ms = (time.monotonic() - t0) * 1000
            log.tool.debug(
                "write_file.execute: exit",
                extra={"_fields": {"path": path_str, "bytes": len(content), "duration_ms": duration_ms}},
            )
            return ToolResult(success=True, output=f"Written: {path_str}", duration_ms=duration_ms)
        except OSError as exc:
            duration_ms = (time.monotonic() - t0) * 1000
            log.tool.error(
                "write_file.execute: OS error",
                exc_info=exc,
                extra={"_fields": {"path": path_str}},
            )
            return ToolResult(success=False, output="", error=str(exc), duration_ms=duration_ms)
