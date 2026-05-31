"""ShellTool — runs shell commands via subprocess, never shell=True (ARCH-75)."""

from __future__ import annotations

import asyncio
import shlex
import time
from pathlib import Path

from stackowl.infra.observability import log
from stackowl.tools.base import Tool, ToolResult

# Default per-command timeout. Raised from the old 30s so an agent-requested
# self-install / longer build / download can complete (Phase D). A per-call
# `timeout` arg overrides this, bounded to _TIMEOUT_CEILING_SEC.
_TIMEOUT_SEC = 120.0
# Hard ceiling: even an agent-requested timeout cannot exceed this, so a single
# command can never wedge the turn indefinitely.
_TIMEOUT_CEILING_SEC = 300.0


def _resolve_timeout(raw: object) -> float:
    """Resolve the effective per-call timeout: default if unset, else bounded.

    Returns ``_TIMEOUT_SEC`` when no timeout is requested; otherwise the requested
    value clamped to (0, _TIMEOUT_CEILING_SEC]. A non-numeric/invalid request falls
    back to the default rather than raising (no-hidden-errors: the command still runs).
    """
    if raw is None:
        return _TIMEOUT_SEC
    try:
        requested = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return _TIMEOUT_SEC
    if requested <= 0:
        return _TIMEOUT_SEC
    return min(requested, _TIMEOUT_CEILING_SEC)

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
                "timeout": {
                    "type": "number",
                    "description": (
                        "Optional per-command timeout in seconds (default "
                        f"{int(_TIMEOUT_SEC)}, bounded to {int(_TIMEOUT_CEILING_SEC)}). "
                        "Raise it for longer installs/downloads/builds."
                    ),
                },
            },
            "required": ["command"],
        }

    async def execute(self, **kwargs: object) -> ToolResult:
        command = str(kwargs.get("command", ""))
        workdir = str(kwargs.get("workdir", "")) or None
        timeout_sec = _resolve_timeout(kwargs.get("timeout"))
        log.tool.debug(
            "shell.execute: entry",
            extra={"_fields": {"command": command[:200], "timeout_sec": timeout_sec}},
        )
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
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
        except TimeoutError:
            duration_ms = (time.monotonic() - t0) * 1000
            log.tool.warning(
                "shell.execute: timeout",
                extra={"_fields": {"command": command[:100], "timeout_sec": timeout_sec, "duration_ms": duration_ms}},
            )
            return ToolResult(
                success=False, output="", error=f"Command timed out after {timeout_sec}s", duration_ms=duration_ms
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
