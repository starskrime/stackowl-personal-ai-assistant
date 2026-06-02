"""BwrapArgvBuilder — the bubblewrap command line for one run (the isolation flags).

This assembles the EXACT ``bwrap`` argv that isolates a run. The flags here ARE the
security feature, so each is deliberate and maps to a :class:`SandboxBackend`
invariant:

* ``--unshare-all`` + the explicit ``--unshare-{net,pid,ipc,uts,cgroup}`` — drop
  every namespace (belt-and-braces; ``--unshare-net`` is invariant #3's no-egress
  empty network namespace).
* ``--die-with-parent`` / ``--new-session`` — the sandbox dies with us and has no
  controlling terminal (no TIOCSTI injection back to the host).
* ``--cap-drop ALL`` (when this bwrap supports it) — invariant #7, no capabilities.
* the mount argv from :class:`~stackowl.sandbox.mounts.MountBuilder` — invariant #6,
  a minimal read-only OS runtime plus the run's own RW ``/workspace`` only.
* ``--clearenv`` then ``--setenv`` for ONLY the allowlisted names — invariant #4,
  host secrets never cross; HOME is pinned to ``/workspace`` (never the host HOME).

Rootlessness (invariant #7) is inherent: there is NO ``--uid 0`` / setuid here —
bwrap runs in the caller's unprivileged user namespace. NO process is spawned; this
only computes argv. Linux-only (only reached after the backend confirms the host).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from stackowl.sandbox.mounts import WORKSPACE_MOUNT, MountBuilder
from stackowl.sandbox.ptc.protocol import PTC_SOCK_ENV, in_sandbox_sock_path
from stackowl.sandbox.spec import ExecSpec

__all__ = ["BwrapArgvBuilder"]

# Namespace/isolation flags applied to EVERY run (see module docstring).
_ISOLATION_FLAGS: tuple[str, ...] = (
    "--unshare-all",
    "--unshare-net",  # invariant #3: empty network namespace, no egress
    "--unshare-pid",
    "--unshare-ipc",
    "--unshare-uts",
    "--unshare-cgroup",
    "--die-with-parent",
    "--new-session",  # detach controlling terminal (no TIOCSTI injection)
)
_CAP_DROP_PROBE_TIMEOUT_S = 5.0
# Cached result of the one-time ``--cap-drop`` support probe (None = not yet probed).
_cap_drop_supported: bool | None = None


class BwrapArgvBuilder:
    """Builds the isolation argv for one run. Pure (no spawning). Never raises."""

    def __init__(self) -> None:
        self._mounts = MountBuilder()

    def build(self, spec: ExecSpec, workspace: Path, *, ptc_sock: Path | None = None) -> list[str]:
        """Assemble the bwrap command line (isolation + mounts + env + exec).

        ``workspace`` is the host scratch ``workspace`` dir bound RW at
        ``/workspace`` (the only writable mount). The returned argv ends with
        ``python3 /workspace/main.py``.

        ``ptc_sock`` (E11-S4): the SHORT HOST path of the per-run PTC socket. When
        given, the ONLY extra relaxation is ``--bind <host_sock> /workspace/.ptc.sock``
        plus the ``OWL_PTC_SOCK`` env pointing the in-sandbox ``owl`` stub at it.
        Network isolation (``--unshare-net``) is UNCHANGED; the socket is the single
        controlled channel. ``None`` → no PTC wiring (the prior behaviour).
        """
        bwrap = shutil.which("bwrap") or "bwrap"
        argv = [bwrap, *_ISOLATION_FLAGS]
        # invariant #7: drop all capabilities when this bwrap supports the flag.
        if self._cap_drop_supported():
            argv += ["--cap-drop", "ALL"]
        # invariant #6: minimal read-only OS runtime + the RW scratch as /workspace.
        argv += self._mounts.build(workspace)
        # PTC (optional): the SINGLE extra mount — bind the short host socket to the
        # fixed in-sandbox path. Network stays denied (--unshare-net above unchanged).
        in_sock = in_sandbox_sock_path(WORKSPACE_MOUNT)
        if ptc_sock is not None:
            argv += ["--bind", str(ptc_sock), in_sock]
        # invariant #4: clear the environment, then forward ONLY the allowlisted
        # names (host value passed through); HOME is pinned to the writable workspace.
        argv += ["--clearenv", "--setenv", "HOME", WORKSPACE_MOUNT]
        for name in spec.env_allow:
            if name == "HOME":
                continue  # HOME is pinned to /workspace above; never the host HOME
            value = os.environ.get(name)
            if value is not None:
                argv += ["--setenv", name, value]
        # PTC: point the in-sandbox stub at the bind-mounted socket.
        if ptc_sock is not None:
            argv += ["--setenv", PTC_SOCK_ENV, in_sock]
        argv += ["--chdir", WORKSPACE_MOUNT, "python3", f"{WORKSPACE_MOUNT}/main.py"]
        return argv

    @staticmethod
    def _cap_drop_supported() -> bool:
        """True if this bwrap build understands ``--cap-drop`` (cached). Never raises."""
        global _cap_drop_supported
        if _cap_drop_supported is not None:
            return _cap_drop_supported
        try:
            out = subprocess.run(  # noqa: S603 — fixed argv, read-only help probe
                [shutil.which("bwrap") or "bwrap", "--help"],
                capture_output=True, text=True, timeout=_CAP_DROP_PROBE_TIMEOUT_S, check=False,
            )
            _cap_drop_supported = "--cap-drop" in (out.stdout + out.stderr)
        except (OSError, ValueError, subprocess.SubprocessError):
            _cap_drop_supported = False
        return _cap_drop_supported
