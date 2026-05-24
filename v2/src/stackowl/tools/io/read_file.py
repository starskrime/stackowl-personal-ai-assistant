"""ReadFileTool — reads a file, with path-traversal guard."""

from __future__ import annotations

import os
import time
from pathlib import Path

from stackowl.infra.observability import log
from stackowl.tools.base import Tool, ToolResult


def _data_root() -> Path:
    data_dir = os.environ.get("STACKOWL_DATA_DIR")
    if data_dir:
        return Path(data_dir).resolve()
    import platformdirs

    return Path(platformdirs.user_data_dir("stackowl")).resolve()


def _guard(path: Path) -> bool:
    """Return True if path is safely inside STACKOWL_DATA_DIR."""
    try:
        path.resolve().relative_to(_data_root())
        return True
    except ValueError:
        return False


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
        target = Path(path_str)
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
