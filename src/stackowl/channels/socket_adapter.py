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

from stackowl.channels.base import ChannelAdapter
from stackowl.exceptions import ChannelAlreadyRegisteredError, ChannelNotFoundError
from stackowl.gateway.scanner import IngressMessage
from stackowl.infra.observability import log
from stackowl.ipc.connection import FrameConnection
from stackowl.ipc.frames import ClarifyAskFrame, SendFileFrame, SendTextFrame
from stackowl.ipc.stream_bridge import chunk_to_frame
from stackowl.pipeline.streaming import ResponseChunk

if TYPE_CHECKING:  # pragma: no cover — typing-only imports
    from stackowl.channels.registry import ChannelRegistry
    from stackowl.config.settings import Settings


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
        """Emit ``text`` as a normal SendTextFrame — the health-canary path.

        ponytail: the real telegram adapter's ``send_ephemeral`` sends silent
        (muted) and returns a real message_id so the caller can delete it
        after confirming the send path works. The existing SendTextFrame
        protocol is fire-and-forget (no reply frame — same tradeoff already
        accepted for ``send_text``/``send_file`` in this class), so a message
        sent cross-process cannot be silenced or deleted from here. The
        caller's cleanup (``ProactiveDeliverer._best_effort_delete``) already
        tolerates a delete failure as cosmetic-only, so returning a sentinel
        id is safe — it just means the canary's probe message stays visible
        instead of self-deleting. Upgrade path: a correlated ack/reply frame
        carrying the gateway's real message_id, same as the send-outcome
        upgrade noted in ``register_socket_channel_proxies``.
        """
        await self.send_text(text, chat_id=chat_id)
        return -1

    async def delete_message(self, chat_id: str | int, message_id: int) -> bool:
        """No-op cleanup for a cross-process ephemeral send.

        ponytail: mirrors ``send_ephemeral`` above — this proxy has no ack
        frame carrying a real message_id (``send_ephemeral`` always returns
        the ``-1`` sentinel), so there is nothing on the gateway side this
        call could identify to delete. ``_best_effort_delete`` already
        treats a delete failure as cosmetic-only; returning ``False`` here
        (instead of raising ``AttributeError``) avoids logging a per-tick
        ERROR for an outcome that is not actually a failure. Same upgrade
        path as ``send_ephemeral``: a correlated ack frame would let this
        delete for real.
        """
        return False

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
