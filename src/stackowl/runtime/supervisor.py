"""Supervisor — spawn (and, later, respawn) the restartable core subprocess.

In the two-process split the gateway is durable and owns the client connections;
the core runs the agent logic and is the process that gets exec-replaced on a
code change. The gateway spawns the core via this module as a child process that
re-enters the CLI through the hidden ``__core__`` subcommand and connects back to
the gateway's already-listening socket.

v1 scope is a single spawn (used by the gateway right after it binds the socket).
Crash-respawn with backoff is Phase 5 — the seam is here (``spawn_core`` returns
the live process handle) so the gateway can grow a supervise loop without
touching its assembly.
"""

from __future__ import annotations

import asyncio
import os
import sys

from stackowl.infra.observability import log


async def spawn_core(
    socket_path: str | os.PathLike[str],
    *,
    env: dict[str, str] | None = None,
) -> asyncio.subprocess.Process:
    """Launch the core process, pointed at ``socket_path``.

    The child re-enters this package's CLI at the hidden ``__core__`` subcommand
    (``python -m stackowl __core__``) so it shares the exact same boot path as a
    normal serve — only its process *role* differs. The socket path is passed via
    ``STACKOWL_CORE_SOCKET`` so the child resolves the identical endpoint without
    re-deriving config.
    """
    child_env = dict(os.environ if env is None else env)
    child_env["STACKOWL_CORE_SOCKET"] = str(socket_path)
    log.gateway.info(
        "[ipc] supervisor: spawning core process",
        extra={"_fields": {"socket_path": str(socket_path)}},
    )
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "stackowl",
        "__core__",
        env=child_env,
    )
    log.gateway.info(
        "[ipc] supervisor: core process spawned",
        extra={"_fields": {"pid": proc.pid}},
    )
    return proc
