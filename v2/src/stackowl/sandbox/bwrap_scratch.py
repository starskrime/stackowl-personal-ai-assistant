"""BwrapScratch — the per-run scratch workspace + naming for the bwrap backend.

Split out of :mod:`stackowl.sandbox.bwrap` (B2 ≤300), mirroring the
``docker_scratch`` extraction. Owns the on-host scratch that holds the run's
``workspace/main.py`` plus the cgroup marker, and the filesystem-safe tag reused for
the cgroup unit name. The scratch root holds ``workspace/`` (the ONLY bind-mounted,
writable dir) and the cgroup marker (kept in the root, NEVER exposed to the child —
invariant #6). All state lives under ``~/.stackowl/sandbox`` (all-state-in-home), is
0700, and is removed after the run. Never raises.
"""

from __future__ import annotations

import contextlib
import shutil
import uuid
from pathlib import Path

from stackowl.infra.observability import log
from stackowl.paths import StackowlHome

__all__ = ["BwrapScratch"]


class BwrapScratch:
    """Creates / writes / cleans up a per-run scratch under ~/.stackowl/sandbox."""

    @staticmethod
    def session_tag(session_id: str) -> str:
        """A filesystem-safe, collision-resistant tag for the scratch + cgroup unit."""
        safe = "".join(c for c in session_id if c.isalnum() or c in "-_")[:32]
        return f"{safe}-{uuid.uuid4().hex[:8]}" if safe else uuid.uuid4().hex[:16]

    @classmethod
    def make(cls, session_id: str) -> Path:
        """Create the 0700 scratch + ``workspace`` subdir; return the scratch ROOT.

        The scratch ROOT holds ``workspace/`` and the cgroup marker; the code goes in
        ``workspace/main.py`` and only ``workspace`` is bind-mounted.
        """
        root = StackowlHome.home() / "sandbox"
        root.mkdir(parents=True, exist_ok=True)
        scratch = root / cls.session_tag(session_id)
        (scratch / "workspace").mkdir(parents=True, exist_ok=True)
        for d in (scratch, scratch / "workspace"):
            with contextlib.suppress(OSError):
                d.chmod(0o700)
        return scratch

    @staticmethod
    def write_code(scratch: Path, code: str) -> None:
        """Write the run's python entrypoint into the scratch workspace."""
        (scratch / "workspace" / "main.py").write_text(code, encoding="utf-8")

    @staticmethod
    def cleanup(scratch: Path | None) -> None:
        """Remove the run's scratch tree. Never raises."""
        if scratch is None:
            return
        try:
            shutil.rmtree(scratch, ignore_errors=True)
        except OSError as exc:
            log.tool.debug(
                "[sandbox.bwrap] _cleanup_scratch: rmtree failed",
                extra={"_fields": {"err": str(exc)}},
            )
