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

from stackowl.ipc.connection import FrameConnection

ConnectionHandler = Callable[[FrameConnection], Awaitable[None]]


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
        # Remove a stale socket file from a prior run; start_unix_server would
        # otherwise fail with EADDRINUSE.
        with contextlib.suppress(FileNotFoundError):
            self._path.unlink()
        self._server = await asyncio.start_unix_server(
            self._on_connect, path=str(self._path)
        )

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
