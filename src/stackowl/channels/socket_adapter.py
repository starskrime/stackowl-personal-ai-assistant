"""SocketChannelAdapter — the core process's channel, bridged to the gateway.

In the split, the core runs the *entire* existing pipeline unchanged; its only
"channel" is this adapter, which carries I/O over the gateway connection instead
of a terminal/bot:

* ``receive()`` yields IngressMessages fed in (``feed``) from inbound
  IngressFrames by the core's frame loop.
* ``send_text`` emits a SendTextFrame stamped with this adapter's channel so the
  gateway routes the ack/clarify back to the originating real adapter.
* ``send`` forwards any chunks as ChunkFrames (defensive — in the socket-registry
  delivery path the turn's output already streams via ``SocketStreamWriter``, so
  the reader handed to ``send`` is empty).

One adapter is bound per originating channel name (``cli``/``telegram``/…) so
acks route home; the per-turn answer stream routes by ``trace_id`` regardless.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from stackowl.channels.base import ChannelAdapter
from stackowl.gateway.scanner import IngressMessage
from stackowl.ipc.connection import FrameConnection
from stackowl.ipc.frames import ClarifyAskFrame, SendFileFrame, SendTextFrame
from stackowl.ipc.stream_bridge import chunk_to_frame
from stackowl.pipeline.streaming import ResponseChunk


class SocketChannelAdapter(ChannelAdapter):
    """A ChannelAdapter whose I/O is the gateway<->core socket."""

    def __init__(self, conn: FrameConnection, channel_name: str = "socket") -> None:
        self._conn = conn
        self._channel = channel_name
        self._inbox: asyncio.Queue[IngressMessage] = asyncio.Queue()

    @property
    def channel_name(self) -> str:
        return self._channel

    def feed(self, msg: IngressMessage) -> None:
        """Push an inbound message (called by the core frame loop)."""
        self._inbox.put_nowait(msg)

    async def receive(self) -> IngressMessage:
        return await self._inbox.get()

    async def send(self, chunks: AsyncIterator[ResponseChunk]) -> None:
        async for chunk in chunks:
            await self._conn.send(chunk_to_frame(chunk))

    async def send_text(self, text: str, *, chat_id: str | int | None = None) -> None:
        """Emit a SendTextFrame so the gateway's real adapter delivers the text.

        Accepts the ``chat_id`` keyword (mirroring ``send_file``) so a
        proactive/notification text reaches a specific chat.
        """
        await self._conn.send(
            SendTextFrame(channel=self._channel, text=text, target=chat_id)
        )

    async def send_file(
        self,
        file_path: str,
        caption: str | None = None,
        *,
        chat_id: str | int | None = None,
    ) -> None:
        """Emit a SendFileFrame so the gateway's real adapter uploads the file.

        Accepts the ``chat_id`` keyword (the ``_TargetedFileSender`` shape the
        notification deliverer narrows to) so a file reaches a specific chat; the
        gateway forwards it to the originating channel's adapter.
        """
        await self._conn.send(
            SendFileFrame(
                channel=self._channel,
                file_path=file_path,
                caption=caption,
                target=chat_id,
            )
        )

    async def send_clarify(
        self,
        session_id: str,
        question: str,
        choices: tuple[str, ...] | list[str],
        clarify_id: str,
    ) -> None:
        """Emit a ClarifyAskFrame so the gateway renders it on the real channel.

        Carries the originating channel + choices so the gateway can render
        tap-buttons (the answer round-trips as a ClarifyReplyFrame). The core's
        ClarifyGateway has the parked turn keyed by clarify_id.
        """
        await self._conn.send(
            ClarifyAskFrame(
                clarify_id=clarify_id,
                session_id=session_id,
                question=question,
                trace_id="",
                channel=self._channel,
                choices=tuple(choices),
            )
        )
