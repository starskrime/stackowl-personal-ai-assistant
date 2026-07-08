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
import contextlib
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING
from uuid import uuid4

from stackowl.channels.base import ChannelAdapter
from stackowl.exceptions import ChannelAlreadyRegisteredError, ChannelNotFoundError
from stackowl.gateway.scanner import IngressMessage
from stackowl.infra.observability import log
from stackowl.ipc.connection import FrameConnection
from stackowl.ipc.frames import (
    ClarifyAskFrame,
    DeleteMessageFrame,
    SendEphemeralFrame,
    SendFileFrame,
    SendTextFrame,
)
from stackowl.ipc.stream_bridge import chunk_to_frame
from stackowl.pipeline.streaming import ResponseChunk

if TYPE_CHECKING:  # pragma: no cover — typing-only imports
    from stackowl.channels.registry import ChannelRegistry
    from stackowl.config.settings import Settings

_EPHEMERAL_ACK_TIMEOUT_SECONDS = 10.0

# One process-wide waiter map: the core process has exactly one gateway
# connection, so a SendEphemeralFrame's request_id is enough to correlate the
# gateway's EphemeralSentFrame reply back to the awaiting caller regardless of
# which per-channel SocketChannelAdapter instance sent it.
_ephemeral_waiters: dict[str, asyncio.Future[int]] = {}


def resolve_ephemeral_sent(request_id: str, message_id: int) -> None:
    """Resolve a pending ``send_ephemeral`` call from an inbound EphemeralSentFrame."""
    future = _ephemeral_waiters.get(request_id)
    if future is None or future.done():
        return
    future.set_result(message_id)


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

    async def send_ephemeral(self, chat_id: str | int, text: str) -> int:
        """Round-trip a muted/self-deleting send through the gateway's real adapter.

        Sends a ``SendEphemeralFrame`` and awaits the correlated
        ``EphemeralSentFrame`` (resolved by :func:`resolve_ephemeral_sent` from
        the core frame loop) carrying the gateway's real Telegram message_id, so
        a later ``delete_message`` can actually remove it. Falls back to the
        ``-1`` sentinel (message never deletable) on timeout or send failure —
        the caller's cleanup already tolerates a delete no-op as cosmetic-only.
        """
        request_id = uuid4().hex
        loop = asyncio.get_running_loop()
        future: asyncio.Future[int] = loop.create_future()
        _ephemeral_waiters[request_id] = future
        log.tool.debug(
            "socket_adapter.send_ephemeral: entry",
            extra={"_fields": {"request_id": request_id, "channel": self._channel}},
        )
        try:
            await self._conn.send(
                SendEphemeralFrame(
                    request_id=request_id,
                    channel=self._channel,
                    text=text,
                    target=chat_id,
                )
            )
            message_id = await asyncio.wait_for(
                future, timeout=_EPHEMERAL_ACK_TIMEOUT_SECONDS
            )
            log.tool.debug(
                "socket_adapter.send_ephemeral: exit",
                extra={"_fields": {"request_id": request_id, "message_id": message_id}},
            )
            return message_id
        except Exception as exc:  # noqa: BLE001 — probe send must never crash the canary tick
            log.tool.error(
                "socket_adapter.send_ephemeral: no ack — falling back to sentinel id",
                exc_info=exc,
                extra={"_fields": {"request_id": request_id, "channel": self._channel}},
            )
            return -1
        finally:
            _ephemeral_waiters.pop(request_id, None)

    async def delete_message(self, chat_id: str | int, message_id: int) -> bool:
        """Fire-and-forget delete of a previously-sent ephemeral message.

        No reply frame round-trips back — mirrors the real adapter's own
        ``delete_message`` contract (a delete failure is cosmetic cleanup, not a
        delivery failure). ``message_id < 0`` means ``send_ephemeral`` never got
        a real id (sentinel fallback), so there is nothing to delete.
        """
        if message_id < 0:
            return False
        await self._conn.send(
            DeleteMessageFrame(
                channel=self._channel, target=chat_id, message_id=message_id
            )
        )
        return True

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


def configured_gateway_channels(settings: Settings) -> list[str]:
    """Names of the remote channels the GATEWAY process is configured to run.

    Mirrors the orchestrator's per-channel start gates (the ``self._role != 'core'
    and <cfg>`` conditions for telegram/slack/discord/whatsapp). The core process
    never constructs these real adapters, but it must know their names to
    pre-register socket proxies so a proactive/scheduled send can resolve the
    channel and route its frame to the gateway's real adapter instead of raising
    ``ChannelNotFoundError``.

    ponytail: duplicates the 4 orchestrator gate conditions — keep in sync. A
    single source of truth would need the gateway to advertise its live channels
    to the core over IPC (larger change; not warranted for 4 static gates).
    """
    channels: list[str] = []
    tg = settings.telegram_channel
    if getattr(tg, "bot_token", None):
        channels.append("telegram")
    slack = settings.slack_channel
    if getattr(slack, "bot_token", None) and getattr(slack, "app_token", None):
        channels.append("slack")
    discord = settings.discord_channel
    if getattr(discord, "enabled", False) and getattr(discord, "bot_token", None):
        channels.append("discord")
    whatsapp = settings.whatsapp_channel
    if getattr(whatsapp, "enabled", False):
        channels.append("whatsapp")
    return channels


def register_socket_channel_proxies(
    registry: ChannelRegistry, conn: FrameConnection, settings: Settings
) -> list[str]:
    """Pre-register a :class:`SocketChannelAdapter` proxy per configured gateway channel.

    ROOT-CAUSE FIX: in split mode the core registers a socket proxy only reactively
    — when an inbound message for that channel first arrives (``_core_frame_loop``).
    Proactive/scheduled sends (``telegram_canary``, ``morning_brief``, ``check_in``,
    ``notification_digest``, goal-execution delivery) fire from the scheduler with NO
    prior inbound message, so ``registry.get('telegram')`` raised
    ``ChannelNotFoundError`` and every proactive send failed. Pre-registering a proxy
    for each channel the gateway is configured to run makes those sends resolve and
    route a ``SendTextFrame`` / ``SendFileFrame`` across the socket to the gateway's
    real adapter.

    Idempotent: a channel already present in the registry (the reactive path won the
    race) is skipped. Returns the channel names newly registered.

    ponytail: fire-and-forget — a gateway-side send failure AFTER the frame is queued
    is not propagated back into the core's ``ProactiveDeliveryOutcome`` (matches the
    existing split out-of-band semantics for SendTextFrame/inbound acks). Upgrade path
    if honest end-to-end delivery status is needed: a correlated
    ``ProactiveSendFrame``/``ProactiveResultFrame`` ack round-trip.
    """
    # 1. ENTRY
    configured = configured_gateway_channels(settings)
    log.gateway.debug(
        "[ipc] socket proxy registration: entry",
        extra={"_fields": {"configured": configured}},
    )
    registered: list[str] = []
    for channel in configured:
        # 2. DECISION — skip a channel the reactive inbound path already registered.
        try:
            registry.get(channel)
        except ChannelNotFoundError:
            pass
        else:
            continue
        # 3. STEP — register a socket proxy so proactive sends resolve this channel.
        with contextlib.suppress(ChannelAlreadyRegisteredError):
            registry.register(SocketChannelAdapter(conn, channel_name=channel))
            registered.append(channel)
    # 4. EXIT
    log.gateway.info(
        "[ipc] socket proxy registration: exit",
        extra={"_fields": {"registered": registered}},
    )
    return registered
