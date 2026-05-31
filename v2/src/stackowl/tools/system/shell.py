"""ShellTool — runs shell commands via subprocess, never shell=True (ARCH-75).

Maximum-autonomy model: ANY command runs SILENTLY (install/network/write — no
prompt, no allowlist). Only a narrow set of truly catastrophic, system-destroying
command shapes (``rm -rf`` on a system/home root, ``dd``/``mkfs``/``shred``/
``wipefs`` on a block device, recursive chmod/chown on a system root, a classic
fork bomb) require explicit user approval via the consent gate. When no
interactive user is present to approve, a catastrophic command fails CLOSED
(deny) — it is never auto-refused otherwise. ``shell=False`` +
``create_subprocess_exec`` keep real injection safety (pipes/redirects/chaining
are inert).
"""

from __future__ import annotations

import asyncio
import shlex
import time
from pathlib import Path

from stackowl.infra.observability import log
from stackowl.infra.trace import TraceContext
from stackowl.paths import StackowlHome
from stackowl.pipeline.services import get_services
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

# System / home roots whose recursive deletion is catastrophic. A normal project
# path (``./build``, ``/tmp/scratch``, ``node_modules``) is NOT in here — the
# detector targets only well-known system-destroying shapes, never normal writes.
_SYSTEM_ROOTS: frozenset[str] = frozenset(
    {
        "/",
        "/*",
        "~",
        "~/",
        "$HOME",
        "/home",
        "/home/*",
        "/etc",
        "/usr",
        "/bin",
        "/sbin",
        "/var",
        "/boot",
        "/root",
        "/lib",
        "/lib32",
        "/lib64",
        "/libx32",
        "/opt",
        "/sys",
        "/proc",
        "/dev",
    }
)


def _is_recursive_force(flags: list[str]) -> bool:
    """True if a flag set requests both recursive AND force (rm -rf shapes)."""
    recursive = False
    force = False
    for tok in flags:
        if tok in ("--recursive",):
            recursive = True
        elif tok in ("--force",):
            force = True
        elif tok.startswith("--"):
            continue
        elif tok.startswith("-"):
            letters = tok[1:]
            if "r" in letters or "R" in letters:
                recursive = True
            if "f" in letters:
                force = True
    return recursive and force


def _is_recursive(flags: list[str]) -> bool:
    """True if a flag set requests recursion (chmod/chown -R shapes)."""
    for tok in flags:
        if tok == "--recursive":
            return True
        if tok.startswith("--"):
            continue
        if tok.startswith("-") and ("r" in tok[1:] or "R" in tok[1:]):
            return True
    return False


def _default_workspace_cwd() -> str | None:
    """The workspace dir to use as the subprocess CWD when no ``workdir`` is given.

    Files a command writes by relative name then land in the workspace, where
    ``send_file``/``write_file`` expect them. Self-healing (B5): if the dir cannot
    be created, log and fall back to ``None`` (process CWD) rather than crashing.
    """
    try:
        ws = StackowlHome.workspace()
        ws.mkdir(parents=True, exist_ok=True)
        return str(ws)
    except OSError as exc:  # B5 — never let a cwd-prep failure crash the command
        log.tool.warning(
            "shell.execute: workspace cwd unavailable — using process cwd",
            extra={"_fields": {"error": str(exc)}},
        )
        return None


def _split_flags_and_operands(rest: list[str]) -> tuple[list[str], list[str]]:
    """Partition the tail of a command into option flags and positional operands."""
    flags = [tok for tok in rest if tok.startswith("-")]
    operands = [tok for tok in rest if not tok.startswith("-")]
    return flags, operands


def is_catastrophic(args: list[str]) -> tuple[bool, str]:
    """Detect a truly system-destroying command shape (conservative).

    ``args`` is the shlex-split command. Returns ``(True, human_reason)`` on a
    match, ``(False, "")`` otherwise. This is intentionally narrow: it matches
    command STRUCTURE and target PATHS (multilingual-safe — no natural-language
    keywords), and errs toward catching only the obvious catastrophic shapes so
    that normal file writes/deletes (``rm -rf ./build``, ``echo x > f.txt``) run
    silently. ``shell=False`` already neutralizes pipes/redirects/chaining.
    """
    if not args:
        return (False, "")

    base = Path(args[0]).name
    rest = args[1:]

    # Fork bomb — the classic ``:(){ :|:& };:`` definition token. shell=False
    # largely neutralizes it, but flag it anyway (defense in depth).
    if any(tok.startswith(":(){") or tok == ":(){" for tok in args):
        return (True, "fork bomb")

    # rm -rf <system/home root>
    if base == "rm":
        flags, operands = _split_flags_and_operands(rest)
        if _is_recursive_force(flags) and any(op in _SYSTEM_ROOTS for op in operands):
            target = next(op for op in operands if op in _SYSTEM_ROOTS)
            return (True, f"recursive force-delete of a system root: {target}")

    # dd of=/dev/... — overwriting a block device
    if base == "dd":
        for tok in rest:
            if tok.startswith("of=") and tok[3:].startswith("/dev/"):
                return (True, f"dd writing to a block device: {tok}")

    # mkfs / mkfs.* / wipefs / shred targeting a /dev device
    if base == "mkfs" or base.startswith("mkfs.") or base in ("wipefs", "shred"):
        _flags, operands = _split_flags_and_operands(rest)
        if any(op.startswith("/dev/") for op in operands):
            dev = next(op for op in operands if op.startswith("/dev/"))
            return (True, f"{base} targeting a device: {dev}")

    # chmod/chown -R on a system root
    if base in ("chmod", "chown"):
        flags, operands = _split_flags_and_operands(rest)
        if _is_recursive(flags) and any(op in _SYSTEM_ROOTS for op in operands):
            target = next(op for op in operands if op in _SYSTEM_ROOTS)
            return (True, f"recursive {base} on a system root: {target}")

    return (False, "")


async def _gate_catastrophic(
    *, tool_name: str, command: str, reason: str
) -> ToolResult | None:
    """Require consent for a catastrophic command shape.

    Shared by :class:`ShellTool` and any tool that runs argv through
    :func:`run_argv` (e.g. a learned tool). Returns a structured declined
    :class:`ToolResult` when the command must NOT run (no user present, no gate,
    gate error, or user denial); returns ``None`` when the user approved and the
    command may proceed. Fail-closed everywhere — any error path denies and never
    spawns.
    """
    ctx = TraceContext.get()
    interactive = bool(ctx.get("interactive", False))
    channel = ctx.get("channel")
    session_id = ctx.get("session_id")
    log.tool.warning(
        "shell.execute: catastrophic command — requiring consent",
        extra={"_fields": {"reason": reason, "interactive": interactive, "channel": channel}},
    )

    # No interactive user to approve → fail closed (deny). Never auto-run.
    if not interactive or not session_id or not channel:
        log.tool.error(
            "shell.execute: catastrophic command and no user present — refused (fail closed)",
            extra={"_fields": {"reason": reason, "interactive": interactive}},
        )
        return ToolResult(
            success=False,
            output="",
            error=(
                "refused: catastrophic command and no user present to approve — "
                f"reason: {reason}"
            ),
            duration_ms=0,
        )

    gate = get_services().consent_gate
    if gate is None:
        log.tool.error(
            "shell.execute: catastrophic command but NO consent gate wired — refused",
            extra={"_fields": {"reason": reason}},
        )
        return ToolResult(
            success=False,
            output="",
            error=f"refused: catastrophic command and no consent gate available — reason: {reason}",
            duration_ms=0,
        )

    try:
        allowed = await gate.policy.request(
            tool_name=tool_name,
            channel=channel,
            session_id=session_id,
            category="catastrophic",
            summary=f"Run shell command: {command}",
        )
    except Exception as exc:  # no-hidden-errors — fail closed on any gate error
        log.tool.error(
            "shell.execute: consent gate raised — refused (fail closed)",
            exc_info=exc,
            extra={"_fields": {"reason": reason}},
        )
        return ToolResult(
            success=False,
            output="",
            error=f"refused: consent check failed — reason: {reason}",
            duration_ms=0,
        )

    if not allowed:
        log.tool.info(
            "shell.execute: catastrophic command declined by user",
            extra={"_fields": {"reason": reason}},
        )
        return ToolResult(
            success=False,
            output="",
            error=f"declined by user — reason: {reason}",
            duration_ms=0,
        )

    log.tool.info(
        "shell.execute: catastrophic command approved — proceeding",
        extra={"_fields": {"reason": reason}},
    )
    return None


async def run_argv(
    argv: list[str],
    *,
    tool_name: str = "shell",
    workdir: str | None = None,
    timeout_sec: float = _TIMEOUT_SEC,
) -> ToolResult:
    """Run an already-split argv through the allowlisted subprocess boundary.

    The SINGLE execution seam shared by :class:`ShellTool` and learned tools: the
    catastrophic-shape check + consent path, then ``create_subprocess_exec``
    (``shell=False`` — pipes/redirects/chaining are inert), timeout, and OSError
    handling, with 4-point logging. Honors the workspace-CWD default (no
    ``workdir`` → workspace). Never raises — every failure becomes a structured
    failed :class:`ToolResult`.
    """
    t0 = time.monotonic()
    if not argv:
        return ToolResult(success=False, output="", error="Empty command", duration_ms=0)

    cwd = workdir or _default_workspace_cwd()
    rendered = " ".join(argv)
    log.tool.debug(
        "shell.execute: entry",
        extra={"_fields": {"command": rendered[:200], "timeout_sec": timeout_sec}},
    )

    # CATASTROPHIC gate — every command runs silently EXCEPT a narrow set of
    # system-destroying shapes, which require the user's explicit approval.
    catastrophic, reason = is_catastrophic(argv)
    if catastrophic:
        decision = await _gate_catastrophic(tool_name=tool_name, command=rendered, reason=reason)
        if decision is not None:
            return decision  # refused / declined / fail-closed — never spawns

    log.tool.debug("shell.execute: launching subprocess", extra={"_fields": {"args": argv[:5]}})
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd or None,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
    except TimeoutError:
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.warning(
            "shell.execute: timeout",
            extra={"_fields": {"command": rendered[:100], "timeout_sec": timeout_sec, "duration_ms": duration_ms}},
        )
        return ToolResult(
            success=False, output="", error=f"Command timed out after {timeout_sec}s", duration_ms=duration_ms
        )
    except OSError as exc:
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.error("shell.execute: OS error", exc_info=exc, extra={"_fields": {"command": rendered[:100]}})
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


class ShellTool(Tool):
    """Run any shell command in a subprocess; catastrophic ones need consent."""

    @property
    def name(self) -> str:
        return "shell"

    @property
    def description(self) -> str:
        return (
            "Run any shell command in a subprocess (shell=False, never shell=True). "
            "Installs, downloads, network and file writes run silently with no "
            "prompt. Only truly catastrophic, system-destroying commands "
            "(rm -rf on a system/home root, dd/mkfs/shred/wipefs on a device, "
            "recursive chmod/chown on a system root, fork bombs) require the user's "
            "explicit approval; if no user is present to approve, they are refused."
        )

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": (
                        "Shell command string. Any command runs silently — including "
                        "installs, downloads, network calls and file writes. Only "
                        "catastrophic, system-destroying commands require the user's "
                        "explicit approval before they run."
                    ),
                },
                "workdir": {
                    "type": "string",
                    "description": (
                        "Working directory (optional; defaults to the StackOwl "
                        "workspace directory, where files written by relative name land)."
                    ),
                },
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
        # No explicit workdir → default the subprocess CWD to the workspace, so a
        # file the command writes by relative name lands where send_file/write_file
        # expect it. An explicit non-empty workdir still wins.
        workdir = str(kwargs.get("workdir", "")) or None
        timeout_sec = _resolve_timeout(kwargs.get("timeout"))
        try:
            args = shlex.split(command)
        except ValueError as exc:
            return ToolResult(success=False, output="", error=f"Invalid command syntax: {exc}", duration_ms=0)
        # The shared seam does the catastrophic-shape check + consent path, the
        # workspace-CWD default, create_subprocess_exec, timeout and OSError
        # handling (the learned-tool path runs the SAME seam).
        return await run_argv(args, tool_name="shell", workdir=workdir, timeout_sec=timeout_sec)
