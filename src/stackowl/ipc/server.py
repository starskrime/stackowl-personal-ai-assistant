"""IpcServer — the gateway-side (durable) unix-domain socket listener.

The gateway owns the listener so a core restart never drops it: when the core
process exec-replaces itself the old connection EOFs and the new core simply
reconnects, producing a fresh accept. The server hands each accepted connection
to an async handler.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import Awaitable, Callable
from pathlib import Path

from stackowl.infra.observability import log
from stackowl.ipc.connection import FrameConnection

ConnectionHandler = Callable[[FrameConnection], Awaitable[None]]


#: How long to wait for an answer before calling the socket residue. Short: this
#: runs on every boot, and a live server accepts immediately.
_PROBE_TIMEOUT_S = 1.0


class IpcServer:
    """Listens on a unix-domain socket and serves one connection at a time."""

    def __init__(self, socket_path: str | os.PathLike[str]) -> None:
        self._path = Path(socket_path)
        self._handler: ConnectionHandler | None = None
        self._server: asyncio.AbstractServer | None = None

    @property
    def socket_path(self) -> Path:
        return self._path

    async def start(self, handler: ConnectionHandler) -> None:
        """Bind the socket and begin accepting. Unlinks any stale socket file."""
        self._handler = handler
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # ASK WHETHER ANYTHING ANSWERS BEFORE REMOVING THE FILE. This used to
        # unlink unconditionally, commented "remove a stale socket file from a
        # prior run" — the right intent, but `unlink` cannot tell a stale socket
        # from a LIVE one. MEASURED 2026-09-04: two servers on one path BOTH bound
        # and the second reported success, so a stray `python -m stackowl start`
        # silently stole the running gateway's endpoint. The first kept running,
        # every new client reached the second, and both would poll Telegram with
        # the same bot credential — D12.7's footgun, arrived at from below.
        #
        # Existence alone must NOT refuse: a unix socket file outlives a process
        # killed with -9, and refusing on the file would strand the platform after
        # any hard kill — the incident start.sh's own header records. The question
        # is whether anything ANSWERS, and the only way to know is to try.
        if await self._is_live():
            raise OSError(
                f"the IPC socket is already in use by a running instance: "
                f"{self._path} — stop it first (./start.sh does this), or that "
                f"instance would keep running unreachable while this one took "
                f"its place"
            )
        with contextlib.suppress(FileNotFoundError):
            self._path.unlink()
        self._server = await asyncio.start_unix_server(
            self._on_connect, path=str(self._path)
        )

    async def _is_live(self) -> bool:
        """Is another server accepting on this path right now?

        A refused or absent connection means the file is residue and may be
        reclaimed. Never raises: an unexpected error is treated as NOT live, so a
        probe that cannot decide can never be the reason the platform will not
        boot.
        """
        if not self._path.exists():
            return False
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(str(self._path)), timeout=_PROBE_TIMEOUT_S
            )
        except (ConnectionRefusedError, FileNotFoundError):
            return False  # residue from a process that is gone
        except TimeoutError:
            log.gateway.warning(
                "[ipc] server._is_live: the socket did not answer in time — "
                "treating it as stale and reclaiming it",
                extra={"_fields": {"path": str(self._path)}},
            )
            return False
        except Exception as exc:  # B5 — never let the probe block a boot
            log.gateway.warning(
                "[ipc] server._is_live: probe failed — treating the socket as stale",
                exc_info=exc, extra={"_fields": {"path": str(self._path)}},
            )
            return False
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
        log.gateway.info(
            "[ipc] server._is_live: another instance is answering on this socket",
            extra={"_fields": {"path": str(self._path)}},
        )
        return True

    async def _on_connect(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        conn = FrameConnection(reader, writer)
        assert self._handler is not None
        try:
            await self._handler(conn)
        finally:
            await conn.aclose()

    async def stop(self) -> None:
        """Stop accepting, close the listener, and remove the socket file."""
        if self._server is not None:
            self._server.close()
            # Bound wait_closed: it only returns once every accepted connection's
            # handler has finished, which can lag during a concurrent teardown.
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._server.wait_closed(), timeout=2.0)
            self._server = None
        with contextlib.suppress(FileNotFoundError):
            self._path.unlink()
