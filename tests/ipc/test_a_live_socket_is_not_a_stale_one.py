"""A second instance must not silently steal a running one's socket.

D12.7 asks for "token locks — cheap protection against a real footgun": two
profiles using one bot credential. MEASURED 2026-09-04, the footgun exists on this
platform one layer lower and with no lock at all.

``IpcServer.start`` unlinks the socket path before binding, commented "Remove a
stale socket file from a prior run; start_unix_server would otherwise fail with
EADDRINUSE". The intent is right and the code cannot tell a STALE socket from a
LIVE one — ``unlink`` succeeds either way. Proven by experiment before this test
was written: two servers on one path both bind, and the second reports success.

WHAT THAT COSTS. The first gateway keeps running while every new client reaches
the second, and both then poll Telegram with the same bot token — which is
exactly D12.7's footgun, except silent. `start.sh` stops the old instance first,
so the damage needs only a direct `python -m stackowl start`, which is a normal
thing to type.

WHY EADDRINUSE ALONE IS NOT THE ANSWER. A unix socket file outlives a process
killed with -9, so refusing whenever the file exists would strand the platform
after any hard kill — the incident already recorded in start.sh's own header, an
orphaned core holding a port across a restart. The distinction that matters is
not "does the file exist" but "does anything ANSWER on it", and the only way to
know is to try.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from stackowl.ipc.connection import FrameConnection
from stackowl.ipc.server import IpcServer

pytestmark = pytest.mark.asyncio


async def _noop(_conn: FrameConnection) -> None:
    return None


def _fresh_path() -> Path:
    return Path(tempfile.mkdtemp()) / "core.sock"


async def test_a_second_server_on_a_LIVE_socket_is_refused() -> None:
    """The defect, asserted directly. Before the fix both servers bound."""
    path = _fresh_path()
    first = IpcServer(path)
    await first.start(_noop)
    try:
        second = IpcServer(path)
        with pytest.raises(OSError) as caught:
            await second.start(_noop)
        assert "already" in str(caught.value).lower(), str(caught.value)
    finally:
        await first.stop()


async def test_a_STALE_socket_file_is_still_reclaimed() -> None:
    """The half that must not regress.

    A unix socket file outlives a process killed with -9. Refusing on mere
    existence would strand the platform after any hard kill, which is the
    incident start.sh's own header records.
    """
    path = _fresh_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    first = IpcServer(path)
    await first.start(_noop)
    await first.stop()
    path.touch()  # the file a hard-killed process leaves behind

    reclaimed = IpcServer(path)
    await reclaimed.start(_noop)   # must NOT raise
    try:
        assert path.exists()
    finally:
        await reclaimed.stop()


async def test_the_refusal_NAMES_the_path() -> None:
    """An operator who typed the wrong start command needs to know which socket."""
    path = _fresh_path()
    first = IpcServer(path)
    await first.start(_noop)
    try:
        with pytest.raises(OSError) as caught:
            await IpcServer(path).start(_noop)
        assert str(path) in str(caught.value)
    finally:
        await first.stop()


async def test_a_normal_first_start_is_unaffected() -> None:
    """Vacuity control: a guard that refused every start would pass the tests
    above and stop the platform booting at all."""
    path = _fresh_path()
    server = IpcServer(path)
    await server.start(_noop)
    try:
        assert path.exists()
    finally:
        await server.stop()
