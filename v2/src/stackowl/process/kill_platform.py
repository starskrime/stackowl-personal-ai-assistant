"""Cross-platform process termination + liveness probing.

A process spawned with ``start_new_session=True`` (POSIX) is its own process-group
leader, so terminating the WHOLE group (``os.killpg``) reaps the command and any
children it forked — no orphan leak. On Windows there are no process groups in the
POSIX sense, so we shell out to ``taskkill /T /F`` which kills the tree.

Self-healing: terminating an ALREADY-DEAD pid is a no-op success (logged, never
raised) — a kill that races the process's natural exit must not surface as an
error. Liveness probing is likewise best-effort: a permission error means the pid
EXISTS (alive); only "no such process" means dead.

Everything branches on :data:`sys.platform` so the same code runs on Linux, macOS
and Windows ([[feedback_cross_platform]]).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import sys

from stackowl.infra.observability import log

_IS_WINDOWS = sys.platform.startswith("win")

# How long to wait for a graceful SIGTERM before escalating to SIGKILL (POSIX).
_GRACE_SECONDS = 3.0


def is_pid_alive(pid: int | None) -> bool:
    """Best-effort liveness probe for a host pid (cross-platform).

    POSIX: ``os.kill(pid, 0)`` — raises ``ProcessLookupError`` if dead,
    ``PermissionError`` if alive-but-not-ours (treated as alive). Windows: a
    ``tasklist`` filter (``os.kill(pid, 0)`` is NOT a reliable no-op there).
    Never raises — any unexpected error is logged and treated as "not alive".
    """
    if not pid or pid <= 0:
        return False
    try:
        if _IS_WINDOWS:
            return _windows_pid_alive(pid)
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # The pid exists but is owned by another user — it IS alive.
        return True
    except OSError as exc:  # B5 — never let a probe crash a caller
        log.tool.debug(
            "process.kill.is_pid_alive: probe error — treating as dead",
            extra={"_fields": {"pid": pid, "error": str(exc)}},
        )
        return False


def _windows_pid_alive(pid: int) -> bool:
    """Windows liveness via ``tasklist`` (synchronous, bounded). Never raises."""
    import subprocess  # local import: only on Windows path

    try:
        out = subprocess.run(  # noqa: S603 — fixed argv, no shell
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return str(pid) in out.stdout
    except (OSError, subprocess.SubprocessError) as exc:  # B5
        log.tool.debug(
            "process.kill._windows_pid_alive: tasklist failed — treating as dead",
            extra={"_fields": {"pid": pid, "error": str(exc)}},
        )
        return False


async def terminate_tree(pid: int | None) -> bool:
    """Terminate a process (and its group/tree); SIGKILL-escalate on POSIX.

    Returns ``True`` if a live process was signalled, ``False`` if the pid was
    already dead (a no-op success — never an error). Spawn MUST use
    ``start_new_session=True`` on POSIX so ``getpgid`` yields the command's own
    group. Never raises — every failure path is logged and folded into the
    boolean ([[feedback_no_hidden_errors]] heal path).
    """
    # 1. ENTRY
    log.tool.debug("process.kill.terminate_tree: entry", extra={"_fields": {"pid": pid}})
    if not pid or pid <= 0:
        return False
    if not is_pid_alive(pid):
        # 2. DECISION — already dead: a kill of a dead pid is a no-op success.
        log.tool.debug(
            "process.kill.terminate_tree: pid already dead — no-op success",
            extra={"_fields": {"pid": pid}},
        )
        return False
    if _IS_WINDOWS:
        return await _terminate_windows(pid)
    return await _terminate_posix(pid)


async def _terminate_posix(pid: int) -> bool:
    """POSIX: SIGTERM the process group, wait, then SIGKILL any survivors."""
    try:
        pgid = os.getpgid(pid)
    except ProcessLookupError:
        return False  # raced to exit between the liveness probe and here
    except OSError as exc:  # B5
        log.tool.warning(
            "process.kill._terminate_posix: getpgid failed — falling back to pid kill",
            extra={"_fields": {"pid": pid, "error": str(exc)}},
        )
        pgid = pid  # best-effort: signal the pid directly
    _signal_group(pgid, signal.SIGTERM, pid)
    # 3. STEP — give the group a grace window to exit before SIGKILL.
    deadline = _GRACE_SECONDS
    waited = 0.0
    step = 0.1
    while waited < deadline and is_pid_alive(pid):
        await asyncio.sleep(step)
        waited += step
    if is_pid_alive(pid):
        log.tool.debug(
            "process.kill._terminate_posix: grace elapsed — SIGKILL",
            extra={"_fields": {"pid": pid, "pgid": pgid}},
        )
        _signal_group(pgid, signal.SIGKILL, pid)
    # 4. EXIT
    log.tool.debug("process.kill._terminate_posix: exit", extra={"_fields": {"pid": pid}})
    return True


def _signal_group(pgid: int, sig: signal.Signals, pid: int) -> None:
    """Signal a whole process group; fall back to the bare pid. Never raises."""
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:
        pass  # group already gone — heal
    except OSError as exc:  # B5 — fall back to signalling the pid itself
        log.tool.debug(
            "process.kill._signal_group: killpg failed — trying bare pid",
            extra={"_fields": {"pgid": pgid, "sig": int(sig), "error": str(exc)}},
        )
        with contextlib.suppress(OSError):
            os.kill(pid, sig)


async def _terminate_windows(pid: int) -> bool:
    """Windows: ``taskkill /T /F /PID`` to kill the whole tree. Never raises."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "taskkill", "/T", "/F", "/PID", str(pid),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=10)
        log.tool.debug("process.kill._terminate_windows: exit", extra={"_fields": {"pid": pid}})
        return True
    except (OSError, TimeoutError) as exc:  # B5
        log.tool.warning(
            "process.kill._terminate_windows: taskkill failed",
            extra={"_fields": {"pid": pid, "error": str(exc)}},
        )
        return False
