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
from stackowl.pipeline.streaming import ResponseChunk
from stackowl.runtime.message_bridge import frame_to_ingress

CoreDispatch = Callable[[IngressMessage, "CoreSink"], Awaitable[None]]

# F-39 — visible, internals-free notice emitted on a turn's stream when its
# dispatch crashes, so the channel shows that the turn errored instead of a
# silently truncated or empty answer.
_TURN_FAILURE_NOTICE = (
    "Sorry — something went wrong while handling that request, so it didn't "
    "finish. Please try again."
)


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

    async def emit_failure_notice(self, text: str) -> None:
        """Emit a single visible failure chunk on the turn's stream (F-39).

        No-op once the stream is closed, so a turn that failed AFTER finishing
        its answer is left untouched. Opens the writer if the dispatch crashed
        before writing anything, so an empty turn still shows a visible error.
        The terminating ``is_final`` sentinel is left to ``close_stream``.
        """
        if self._closed:
            return
        writer = self.stream_writer()
        await writer.write(
            ResponseChunk(
                content=text,
                is_final=False,
                chunk_index=-1,
                trace_id=self.request_id,
                owl_name="",
            )
        )

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
            # F-39 — surface a visible failure on the stream so the channel does
            # not deliver a silently truncated/empty answer. Best-effort: a notice
            # that itself fails to emit must not re-raise (keep the link alive).
            with contextlib.suppress(Exception):
                await sink.emit_failure_notice(_TURN_FAILURE_NOTICE)
        finally:
            # Stream-finalize-on-cut: guarantee the gateway reader terminates even
            # if dispatch crashed before closing its own writer.
            with contextlib.suppress(Exception):
                await sink.close_stream()
