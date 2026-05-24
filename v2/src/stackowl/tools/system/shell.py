"""ShellTool — runs shell commands via subprocess, never shell=True (ARCH-75)."""

from __future__ import annotations

import asyncio
import shlex
import time
from pathlib import Path

from stackowl.infra.observability import log
from stackowl.tools.base import Tool, ToolResult

_TIMEOUT_SEC = 30.0

_ALLOWED_COMMANDS: frozenset[str] = frozenset(
    {
        "ls",
        "cat",
        "head",
        "tail",
        "grep",
        "find",
        "echo",
        "pwd",
        "wc",
        "sort",
        "uniq",
        "cut",
        "awk",
        "sed",
        "tr",
        "diff",
        "stat",
        "python3",
        "python",
        "uv",
        "pip",
        "git",
        "make",
    }
)


class ShellTool(Tool):
    """Execute an allowlisted shell command in a subprocess (ARCH-75)."""

    @property
    def name(self) -> str:
        return "shell"

    @property
    def description(self) -> str:
        return "Run an allowlisted shell command. Never uses shell=True."

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command string"},
                "workdir": {"type": "string", "description": "Working directory (optional)"},
            },
            "required": ["command"],
        }

    async def execute(self, **kwargs: object) -> ToolResult:
        command = str(kwargs.get("command", ""))
        workdir = str(kwargs.get("workdir", "")) or None
        log.tool.debug("shell.execute: entry", extra={"_fields": {"command": command[:200]}})
        t0 = time.monotonic()
        try:
            args = shlex.split(command)
        except ValueError as exc:
            return ToolResult(success=False, output="", error=f"Invalid command syntax: {exc}", duration_ms=0)

        if not args:
            return ToolResult(success=False, output="", error="Empty command", duration_ms=0)

        base_cmd = Path(args[0]).name
        if base_cmd not in _ALLOWED_COMMANDS:
            log.tool.warning(
                "shell.execute: command not in allowlist",
                extra={"_fields": {"cmd": base_cmd}},
            )
            return ToolResult(
                success=False,
                output="",
                error=f"Command not allowed: {base_cmd!r}",
                duration_ms=0,
            )

        log.tool.debug("shell.execute: launching subprocess", extra={"_fields": {"args": args[:5]}})
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=workdir or None,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=_TIMEOUT_SEC)
        except TimeoutError:
            duration_ms = (time.monotonic() - t0) * 1000
            log.tool.warning(
                "shell.execute: timeout",
                extra={"_fields": {"command": command[:100], "duration_ms": duration_ms}},
            )
            return ToolResult(
                success=False, output="", error=f"Command timed out after {_TIMEOUT_SEC}s", duration_ms=duration_ms
            )
        except OSError as exc:
            duration_ms = (time.monotonic() - t0) * 1000
            log.tool.error("shell.execute: OS error", exc_info=exc, extra={"_fields": {"command": command[:100]}})
            return ToolResult(success=False, output="", error=str(exc), duration_ms=duration_ms)

        duration_ms = (time.monotonic() - t0) * 1000
        success = proc.returncode == 0
        output = stdout.decode("utf-8", errors="replace").strip()
        error = stderr.decode("utf-8", errors="replace").strip() if not success else None
        log.tool.debug(
            "shell.execute: exit",
            extra={
                "_fields": {
                    "success": success,
                    "returncode": proc.returncode,
                    "output_len": len(output),
                    "duration_ms": duration_ms,
                }
            },
        )
        return ToolResult(success=success, output=output, error=error, duration_ms=duration_ms)
