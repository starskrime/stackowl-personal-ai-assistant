"""FrameConnection — a duplex frame channel over an asyncio stream pair."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import cast

from stackowl.infra.observability import log
from stackowl.ipc.codec import FrameDecodeError, decode_frame, encode_frame
from stackowl.ipc.frames import Frame
from stackowl.runtime.link_auth import PeerCredSocket


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

    @property
    def raw_socket(self) -> PeerCredSocket | None:
        """The underlying socket, for the Spec 2.4 peer-PID check ONLY.

        ``None`` if the transport doesn't expose one (never raises). In
        production this is an ``asyncio.trsock.TransportSocket`` proxy, NOT a
        literal ``socket.socket`` -- ``get_extra_info("socket")`` never
        returns the real one over a real asyncio transport. Typed as
        ``PeerCredSocket`` (structural: anything ``getsockopt``-capable)
        rather than narrowed with ``isinstance(sock, socket.socket)``, which
        would silently discard that real proxy and return ``None`` on every
        real connection -- a regression a real
        ``asyncio.start_unix_server``/``open_unix_connection`` integration
        test caught (``tests/ipc/test_socket_transport.py``).
        """
        sock = self._writer.get_extra_info("socket")
        return cast("PeerCredSocket | None", sock)

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
            except FrameDecodeError as exc:
                # Spec 2.3 — a corrupt/unknown-type line is skipped (never kills
                # the stream) but is never SILENT either: an unknown frame type
                # or a half-upgraded install now says so, naming the type
                # best-effort-extracted from the line (None when the line isn't
                # even valid JSON).
                log.ipc.warning(
                    "[ipc] frame connection: undecodable line — skipping",
                    extra={"_fields": {"frame_type": exc.frame_type, "error": str(exc)}},
                )
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
