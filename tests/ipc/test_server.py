"""Spec 2.4 -- ``IpcServer.start()`` makes the socket's parent directory owner-only.

AD-33/NFR32: the gateway<->core socket directory must be ``0700`` so no other
same-user process can even list the socket file, layered on top of the
peer-PID + link-secret checks in ``runtime.link_auth``/``runtime.gateway_link``.
"""

from __future__ import annotations

import stat
import tempfile
from pathlib import Path

from stackowl.ipc.connection import FrameConnection
from stackowl.ipc.server import IpcServer


async def _noop(_conn: FrameConnection) -> None:
    return None


def _fresh_path() -> Path:
    return Path(tempfile.mkdtemp()) / "runtime" / "core.sock"


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


async def test_start_makes_the_socket_parent_directory_owner_only() -> None:
    path = _fresh_path()
    server = IpcServer(path)
    await server.start(_noop)
    try:
        assert _mode(path.parent) == 0o700
    finally:
        await server.stop()


async def test_start_chmods_an_already_existing_parent_directory_too() -> None:
    """The directory can pre-exist (e.g. shared with dev_ingress's runtime/)
    at default permissions -- `start` must still tighten it, not just create-only."""
    path = _fresh_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o755)
    assert _mode(path.parent) == 0o755

    server = IpcServer(path)
    await server.start(_noop)
    try:
        assert _mode(path.parent) == 0o700
    finally:
        await server.stop()


async def test_a_chmod_failure_does_not_abort_boot(monkeypatch) -> None:  # noqa: ANN001
    """A chmod failure logs ERROR but `start()` must still bind and accept."""
    path = _fresh_path()

    real_chmod = Path.chmod

    def _raising_chmod(self: Path, mode: int) -> None:
        if self == path.parent:
            raise OSError("simulated chmod failure")
        real_chmod(self, mode)

    monkeypatch.setattr(Path, "chmod", _raising_chmod)

    server = IpcServer(path)
    await server.start(_noop)  # must NOT raise
    try:
        assert path.exists()
    finally:
        await server.stop()
