"""FrameConnection — a duplex frame channel over an asyncio stream pair."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from stackowl.ipc.codec import FrameDecodeError, decode_frame, encode_frame
from stackowl.ipc.frames import Frame


class FrameConnection:
    """Send/receive :class:`Frame` objects over a stream reader/writer pair.

    Sends are serialised under a lock so concurrent producer tasks (the chunk
    stream, progress events, clarify asks) never interleave bytes on the wire.
    """

    def __init__(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._send_lock = asyncio.Lock()
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    async def send(self, frame: Frame) -> None:
        """Write one frame and flush. Raises if the connection is closed."""
        if self._closed:
            raise ConnectionError("send on a closed FrameConnection")
        async with self._send_lock:
            self._writer.write(encode_frame(frame))
            await self._writer.drain()

    async def recv(self) -> Frame | None:
        """Read the next frame, or ``None`` on clean EOF (peer hung up).

        Raises :class:`FrameDecodeError` on a malformed line so the caller can
        decide whether to skip or tear down — a corrupt frame is never silently
        dropped.
        """
        line = await self._reader.readline()
        if not line:  # EOF
            self._closed = True
            return None
        return decode_frame(line)

    def __aiter__(self) -> AsyncIterator[Frame]:
        return self._iter()

    async def _iter(self) -> AsyncIterator[Frame]:
        while True:
            try:
                frame = await self.recv()
            except FrameDecodeError:
                # Skip a single corrupt line rather than killing the stream.
                continue
            if frame is None:
                return
            yield frame

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._writer.close()
            # Bound the close handshake: with both peers closing concurrently,
            # wait_closed() can block on the full bidirectional teardown. close()
            # already releases the FD, so awaiting confirmation is best-effort.
            await asyncio.wait_for(self._writer.wait_closed(), timeout=2.0)
        except (ConnectionError, OSError, TimeoutError):
            pass
