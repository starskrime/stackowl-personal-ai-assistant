"""IpcClient — the core-side (restartable) reconnecting socket client.

The core connects to the durable gateway's listener. Because the gateway may
start first OR the core may race ahead of it (and because a fresh core after an
exec-replace must re-attach), ``connect`` retries with backoff until the socket
is accepting or a deadline elapses.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from stackowl.ipc.connection import FrameConnection


class IpcClient:
    """Connects to the gateway's unix-domain socket, with bounded retry."""

    def __init__(self, socket_path: str | os.PathLike[str]) -> None:
        self._path = Path(socket_path)

    @property
    def socket_path(self) -> Path:
        return self._path

    async def connect(
        self,
        *,
        timeout_s: float = 30.0,
        retry_interval_s: float = 0.1,
    ) -> FrameConnection:
        """Open a connection, retrying until ``timeout_s`` elapses.

        Retries on the transient errors seen while the server is still binding
        (the socket file missing, or present but not yet listening).
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s
        last_exc: OSError | None = None
        while True:
            try:
                reader, writer = await asyncio.open_unix_connection(str(self._path))
                return FrameConnection(reader, writer)
            except (FileNotFoundError, ConnectionRefusedError, OSError) as exc:
                last_exc = exc
                if loop.time() >= deadline:
                    raise TimeoutError(
                        f"could not connect to {self._path} within {timeout_s}s"
                    ) from last_exc
                await asyncio.sleep(retry_interval_s)
