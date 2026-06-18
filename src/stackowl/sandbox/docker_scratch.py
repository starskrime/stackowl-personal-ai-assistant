"""DockerScratch — the per-run scratch workspace + naming for the Docker backend.

Split out of :mod:`stackowl.sandbox.docker` (B2 ≤300). Owns the on-host scratch
holding the run's ``main.py`` and the container/scratch naming. The code lives in a
``code`` subdir that is bind-mounted READ-ONLY into the container (invariant #6 — a
payload cannot rewrite its own entrypoint mid-run, and the only writable path is a
separate bounded tmpfs). All state lives under ``~/.stackowl/sandbox`` (all-state-in-
home) and is removed after the run. The dirs are 0755 / the file 0644 so the
NON-ROOT container user (uid 65534) can read the entrypoint. Never raises.
"""

from __future__ import annotations

import contextlib
import shutil
import uuid
from pathlib import Path

from stackowl.infra.observability import log
from stackowl.paths import StackowlHome
from stackowl.sandbox.docker_argv import CODE_FILE

__all__ = ["DockerScratch"]


class DockerScratch:
    """Creates / writes / cleans up a per-run scratch under ~/.stackowl/sandbox."""

    @staticmethod
    def session_tag(session_id: str) -> str:
        """A filesystem/docker-safe, collision-resistant tag for scratch + name."""
        safe = "".join(c for c in session_id if c.isalnum() or c in "-_")[:32]
        return f"{safe}-{uuid.uuid4().hex[:8]}" if safe else uuid.uuid4().hex[:16]

    @classmethod
    def container_name(cls, session_id: str) -> str:
        """A unique, valid ``--name`` for reaping (matches docker's name regex)."""
        return f"stackowl-sbx-{cls.session_tag(session_id)}"

    @classmethod
    def make(cls, session_id: str) -> Path:
        """Create the scratch + ``code`` subdir; return the scratch ROOT.

        The code goes in ``code/main.py`` and ONLY the ``code`` subdir is bind-mounted
        (READ-ONLY). Dirs are 0755 so the non-root container user can traverse them.
        """
        root = StackowlHome.home() / "sandbox"
        root.mkdir(parents=True, exist_ok=True)
        scratch = root / cls.session_tag(session_id)
        code_dir = scratch / "code"
        code_dir.mkdir(parents=True, exist_ok=True)
        for d in (scratch, code_dir):
            with contextlib.suppress(OSError):
                d.chmod(0o755)
        return scratch

    @staticmethod
    def write_code(scratch: Path, code: str) -> None:
        """Write the run's python entrypoint into the scratch ``code`` dir (0644)."""
        target = scratch / "code" / CODE_FILE
        target.write_text(code, encoding="utf-8")
        with contextlib.suppress(OSError):
            target.chmod(0o644)

    @staticmethod
    def cleanup(scratch: Path | None) -> None:
        """Remove the run's scratch tree. Never raises."""
        if scratch is None:
            return
        try:
            shutil.rmtree(scratch, ignore_errors=True)
        except OSError as exc:
            log.tool.debug(
                "[sandbox.docker] scratch.cleanup: rmtree failed",
                extra={"_fields": {"err": str(exc)}},
            )
