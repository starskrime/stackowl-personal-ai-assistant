"""DockerArgvBuilder — the hardened ``docker run`` command line for one run.

Every flag here IS the security feature (each maps to a :class:`SandboxBackend`
invariant); this builder is the single place the hardening set is assembled so it
cannot be partially forgotten. The host Docker daemon is ROOTFUL, so the profile
is deliberately maximal:

* **network (#3)** — ``--network=none`` by DEFAULT (deny). Only ``--network=bridge``
  when the spec explicitly opts in AND the backend supports network. A
  ``network=False`` run gets NO network namespace egress at all.
* **resource caps (#2, mandatory)** — ``--memory <m>m`` + ``--memory-swap`` pinned
  to the SAME value (no swap → a memory hog is OOM-killed, not swapped), ``--cpus``,
  ``--pids-limit``. The daemon rejecting any of these makes the backend refuse.
* **privilege (#7)** — ``--cap-drop=ALL``, ``--security-opt=no-new-privileges``,
  ``--user <non-root uid:gid>`` (runs as ``nobody`` 65534, NEVER container-root),
  and the MANDATORY ``--security-opt seccomp=<profile>`` restrictive filter. Never
  ``seccomp=unconfined``.
* **filesystem (#6)** — ``--read-only`` rootfs; an ephemeral
  ``--tmpfs /tmp`` (noexec,nosuid,nodev, size-bounded); the run's code mounted
  READ-ONLY at ``/workspace`` and a SEPARATE size-bounded writable tmpfs at
  ``/work`` (HOME) so the program has a bounded scratch without a writable code
  mount. NO host-sensitive mounts (no docker.sock, no /home, no ~/.stackowl).
* **env (#4)** — a container inherits no host env; ONLY the allowlisted names are
  forwarded via ``-e NAME=value`` (HOME pinned to the writable ``/work``). Secrets
  never cross.

NO process is spawned here — this only computes argv (the backend runs it). The
code content is never placed on the argv (it lives in the read-only mounted file).
"""

from __future__ import annotations

import os
from pathlib import Path

from stackowl.sandbox.ptc.protocol import PTC_SOCK_ENV, in_sandbox_sock_path
from stackowl.sandbox.spec import ExecSpec

__all__ = ["DockerArgvBuilder"]

# In-container paths. ``/workspace`` holds the code READ-ONLY; ``/work`` is the
# writable HOME scratch (a bounded tmpfs). Keeping them separate means the code
# mount is never writable (a payload cannot rewrite its own entrypoint mid-run).
CODE_MOUNT = "/workspace"
WORK_MOUNT = "/work"
CODE_FILE = "main.py"

# Non-root in-container identity (``nobody:nogroup`` on the slim image). Never 0.
NON_ROOT_UID = 65534
NON_ROOT_GID = 65534

# /tmp tmpfs ceiling (MiB) — small, hardened mount flags.
_TMP_SIZE_MIB = 64


class DockerArgvBuilder:
    """Builds the hardened ``docker run`` argv for one run. Pure. Never raises."""

    def build(
        self,
        *,
        spec: ExecSpec,
        image: str,
        container_name: str,
        code_dir: Path,
        seccomp_profile: Path,
        docker_bin: str = "docker",
        ptc_socket: Path | None = None,
    ) -> list[str]:
        """Assemble the full hardened argv.

        ``code_dir`` is the host dir holding ``main.py`` (bind-mounted READ-ONLY at
        ``/workspace``). ``seccomp_profile`` is the mandatory restrictive filter.
        The returned argv ends with ``<image> python /workspace/main.py``.

        ``ptc_socket`` (E11-S4, optional): the HOST path of the per-run PTC socket.
        When given, the ONLY extra mount is that single socket volume at
        ``/work/.ptc.sock`` plus the ``OWL_PTC_SOCK`` env — ``--network=none`` is
        unchanged. ``None`` → no PTC wiring at all (the prior behaviour).
        """
        caps = spec.caps
        # NOTE: deliberately NO ``--rm`` here. With ``--rm`` the daemon removes the
        # container the instant it exits, which races our ``docker inspect
        # State.OOMKilled`` probe (an OOM would then mis-classify as a bare SIGKILL).
        # Removal is instead guaranteed by the backend's ``docker rm -f`` in a
        # ``finally`` — so the container survives just long enough to be inspected,
        # then is always reaped. No container is ever left behind.
        argv: list[str] = [docker_bin, "run", "--name", container_name]

        # --- network (#3): deny by default; bridge only on explicit opt-in.
        argv += ["--network", "bridge" if spec.network else "none"]

        # --- resource caps (#2): all mandatory; --memory-swap == --memory (no swap).
        argv += [
            "--memory", f"{caps.mem_mib}m",
            "--memory-swap", f"{caps.mem_mib}m",
            "--cpus", str(caps.cpu_cores),
            "--pids-limit", str(caps.pids),
            "--oom-kill-disable=false",
        ]

        # --- privilege (#7): drop everything, no new privs, non-root, seccomp.
        argv += [
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--security-opt", f"seccomp={seccomp_profile}",
            "--user", f"{NON_ROOT_UID}:{NON_ROOT_GID}",
        ]

        # --- filesystem (#6): read-only rootfs; ephemeral hardened /tmp; code RO;
        #     bounded writable HOME tmpfs. No host-sensitive mount whatsoever.
        argv += [
            "--read-only",
            "--tmpfs", f"/tmp:rw,noexec,nosuid,nodev,size={_TMP_SIZE_MIB}m",
            "--tmpfs",
            f"{WORK_MOUNT}:rw,noexec,nosuid,nodev,size={caps.fs_write_mib}m,uid={NON_ROOT_UID},gid={NON_ROOT_GID}",
            "--volume", f"{code_dir}:{CODE_MOUNT}:ro",
            "--workdir", WORK_MOUNT,
        ]

        # --- PTC (#optional): the SINGLE extra mount — the per-run socket — plus its
        #     env pointer. The socket is mounted into the writable /work; net stays none.
        if ptc_socket is not None:
            in_sock = in_sandbox_sock_path(WORK_MOUNT)
            argv += ["--volume", f"{ptc_socket}:{in_sock}"]

        # --- env (#4): allowlist-from-empty; HOME pinned to the writable scratch.
        argv += ["--env", f"HOME={WORK_MOUNT}"]
        for name in spec.env_allow:
            if name == "HOME":
                continue  # HOME is pinned to /work above; never the host HOME
            value = os.environ.get(name)
            if value is not None:
                argv += ["--env", f"{name}={value}"]
        if ptc_socket is not None:
            argv += ["--env", f"{PTC_SOCK_ENV}={in_sandbox_sock_path(WORK_MOUNT)}"]

        # --- exec: the interpreter runs the READ-ONLY mounted entrypoint.
        argv += [image, "python", f"{CODE_MOUNT}/{CODE_FILE}"]
        return argv

    @staticmethod
    def redact_env(argv: list[str]) -> list[str]:
        """Return a copy of ``argv`` with ``--env NAME=value`` values masked.

        The token AFTER an ``--env`` / ``-e`` flag is ``NAME=value``; the name is
        kept (auditable) but the value is replaced with ``***`` so a secret a caller
        forwarded via ``env_allow`` never reaches the logs (sensitive-data rule).
        Never raises.
        """
        redacted: list[str] = []
        prev_was_env = False
        for token in argv:
            if prev_was_env and "=" in token:
                name, _, _ = token.partition("=")
                redacted.append(f"{name}=***")
            else:
                redacted.append(token)
            prev_was_env = token in ("--env", "-e")
        return redacted
