"""MountBuilder — the bubblewrap filesystem-isolation argv (no host secrets).

This builds the read-only/tmpfs mount portion of a ``bwrap`` invocation. The
load-bearing security property is invariant #6 (no host filesystem access beyond
declared mounts): the child sees ONLY a minimal, read-only OS runtime plus its own
read-write scratch ``/workspace`` — never ``/home``, never ``~/.stackowl`` secrets,
never the project tree, never ``/etc`` secret material.

What IS mounted (all read-only except the scratch):

* The host python runtime prefix (``sys.base_prefix`` — ``/usr`` on a system
  python) so ``python3`` and the stdlib resolve, plus the standard library
  directories ``/bin`` ``/lib`` ``/lib64`` when present (the dynamic loader +
  shared libs the interpreter needs).
* A fresh ``--tmpfs /tmp`` (empty, in-memory) — nothing from the host's /tmp.
* A minimal ``--proc /proc`` and ``--dev /dev`` (bwrap's own minimal devtmpfs:
  null/zero/random/urandom/tty only — NOT the host's full /dev).
* The run's scratch directory bind-mounted READ-WRITE at ``/workspace`` (the ONLY
  writable path) with the child chdir'd there.

What is DELIBERATELY NOT mounted: the host ``/home``, the user's ``~/.stackowl``
secrets/workspace, the project source tree, ``/etc`` (no resolv.conf, no shadow,
no ssl private keys) — there is no network, so the child needs none of it. A
secret env var or token never crosses because nothing carrying it is bound and the
environment is cleared (see :mod:`stackowl.sandbox.bwrap`).

NO process is spawned here — this only computes argv. Cross-platform note: bwrap is
Linux-only; this builder is only reached after the backend confirms a Linux host.
"""

from __future__ import annotations

import sys
from pathlib import Path

from stackowl.infra.observability import log

__all__ = ["MountBuilder"]

# The OS runtime directories the interpreter + dynamic loader need, mounted
# READ-ONLY. ``/usr`` is added separately from the resolved python prefix so a
# non-system python (a venv with a different base_prefix) still gets its runtime.
# Every entry here is read-only; none carries host secrets.
_RUNTIME_RO_DIRS: tuple[str, ...] = ("/bin", "/lib", "/lib64", "/usr", "/sbin")

# The scratch is bind-mounted read-write at this fixed in-sandbox path; the child
# is chdir'd here and HOME points at it. It is the ONLY writable location.
WORKSPACE_MOUNT = "/workspace"


class MountBuilder:
    """Builds the bwrap mount argv for one run. Pure (no spawning). Never raises."""

    def __init__(self) -> None:
        # Resolve the host python runtime prefix once. ``sys.base_prefix`` is the
        # install root even inside a venv, so the real interpreter + stdlib resolve.
        self._py_prefix = Path(sys.base_prefix).resolve()

    def build(self, scratch_workspace: Path) -> list[str]:
        """Return the ``--ro-bind``/``--tmpfs``/``--proc``/``--dev``/``--bind`` argv.

        ``scratch_workspace`` is the host directory bind-mounted read-write at
        ``/workspace`` — the run's only writable path. Everything else is read-only
        OS runtime; no host secret path is bound (invariant #6).
        """
        # 1. ENTRY
        log.tool.debug(
            "[sandbox.mounts] build: entry",
            extra={"_fields": {"py_prefix": str(self._py_prefix)}},
        )
        argv: list[str] = []
        bound: set[str] = set()

        # 2. DECISION — bind only existing OS runtime dirs, each read-only. The
        #    resolved python prefix is included so a venv interpreter still works.
        candidates = list(_RUNTIME_RO_DIRS) + [str(self._py_prefix)]
        for raw in candidates:
            path = Path(raw)
            real = str(path)
            if real in bound or not path.exists():
                continue
            bound.add(real)
            argv += ["--ro-bind", real, real]

        # 3. STEP — ephemeral /tmp, minimal /proc + /dev, and the RW scratch. No
        #    host /tmp, no host /dev beyond bwrap's minimal devtmpfs, no /etc.
        argv += ["--tmpfs", "/tmp"]
        argv += ["--proc", "/proc"]
        argv += ["--dev", "/dev"]
        argv += ["--bind", str(scratch_workspace), WORKSPACE_MOUNT]

        # 4. EXIT
        log.tool.debug(
            "[sandbox.mounts] build: exit",
            extra={"_fields": {"ro_binds": len(bound), "workspace": str(scratch_workspace)}},
        )
        return argv
