"""CoreLink — the core process's view of the gateway connection.

The core connects to the durable gateway, announces itself (hello), then serves
inbound IngressFrames: each becomes an ``IngressMessage`` handed to the injected
``dispatch`` together with a per-turn :class:`CoreSink`. The sink is how the
core's turn machinery emits results across the socket — a frame-backed stand-in
for the in-process (adapter, stream writer) pair:

* ``stream_writer()`` -> a :class:`SocketStreamWriter` (the turn's answer/progress
  stream), created once and reused.
* ``send_text()`` -> a proactive/out-of-band message frame.
* ``clarify_ask()`` -> a clarify question frame.

``CoreLink`` guards every dispatch: on return or crash it closes the turn's
stream (if opened and not already closed) so the gateway reader can never hang
on a producer that died mid-turn.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable

from stackowl.gateway.scanner import IngressMessage
from stackowl.infra.observability import log
from stackowl.ipc.connection import FrameConnection
from stackowl.ipc.frames import ClarifyAskFrame, HelloFrame, IngressFrame, SendTextFrame
from stackowl.ipc.stream_bridge import SocketStreamWriter
from stackowl.runtime.message_bridge import frame_to_ingress

CoreDispatch = Callable[[IngressMessage, "CoreSink"], Awaitable[None]]


class CoreSink:
    """Per-turn emit surface for the core's dispatch, backed by the connection."""

    def __init__(self, conn: FrameConnection, request_id: str) -> None:
        self._conn = conn
        self.request_id = request_id
        self._writer: SocketStreamWriter | None = None
        self._closed = False

    def stream_writer(self) -> SocketStreamWriter:
        """The turn's response stream writer (created once, reused)."""
        if self._writer is None:
            self._writer = SocketStreamWriter(self._conn, self.request_id)
        return self._writer

    async def close_stream(self) -> None:
        """Close the turn's stream once (idempotent)."""
        if self._writer is not None and not self._closed:
            self._closed = True
            await self._writer.close()

    async def send_text(
        self, channel: str, text: str, target: int | str | None = None
    ) -> None:
        await self._conn.send(SendTextFrame(channel=channel, text=text, target=target))

    async def clarify_ask(
        self,
        *,
        clarify_id: str,
        session_id: str,
        question: str,
        trace_id: str,
        target: int | str | None = None,
    ) -> None:
        await self._conn.send(
            ClarifyAskFrame(
                clarify_id=clarify_id,
                session_id=session_id,
                question=question,
                trace_id=trace_id,
                target=target,
            )
        )


class CoreLink:
    """Serves inbound IngressFrames from the gateway, dispatching each turn."""

    def __init__(self, conn: FrameConnection, dispatch: CoreDispatch) -> None:
        self._conn = conn
        self._dispatch = dispatch
        self._tasks: set[asyncio.Task[None]] = set()

    async def send_hello(self, core_pid: int) -> None:
        await self._conn.send(HelloFrame(core_pid=core_pid))

    async def run(self) -> None:
        """Receive frames until the gateway hangs up (clean EOF)."""
        async for frame in self._conn:
            if isinstance(frame, IngressFrame):
                msg = frame_to_ingress(frame)
                sink = CoreSink(self._conn, msg.trace_id)
                task = asyncio.create_task(self._guarded_dispatch(msg, sink))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
            else:
                # clarify_reply / stop / steer handling lands with the engine fork.
                log.gateway.debug(
                    "[ipc] core link: unhandled inbound frame",
                    extra={"_fields": {"type": getattr(frame, "type", "?")}},
                )

    async def _guarded_dispatch(self, msg: IngressMessage, sink: CoreSink) -> None:
        try:
            await self._dispatch(msg, sink)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — never let one turn kill the link
            log.gateway.error(
                "[ipc] core link: dispatch failed",
                exc_info=exc,
                extra={"_fields": {"request_id": msg.trace_id, "session_id": msg.session_id}},
            )
        finally:
            # Stream-finalize-on-cut: guarantee the gateway reader terminates even
            # if dispatch crashed before closing its own writer.
            with contextlib.suppress(Exception):
                await sink.close_stream()
