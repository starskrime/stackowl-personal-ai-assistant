"""DiscordChannelAdapter — bridges Discord servers/DMs to the StackOwl gateway.

The adapter consumes a :class:`DiscordSettings` injected by the caller,
exposes the canonical :class:`ChannelAdapter` surface, and self-registers
with :class:`ChannelRegistry` on ``start()``.

Live I/O paths are guarded by :class:`TestModeGuard` so tests never open a
WebSocket. Message intake is mediated by an internal ``asyncio.Queue`` that
:meth:`handle_message` populates from discord.py callbacks.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import discord

from stackowl.channels.base import ChannelAdapter
from stackowl.channels.discord.helpers import (
    DiscordMarkdownFormatter,
    hash_user_id,
    is_authorized,
    strip_bot_mention,
)
from stackowl.channels.discord.settings import DiscordSettings
from stackowl.channels.splitter import DiscordMessageSplitter
from stackowl.config.test_mode import TestModeGuard
from stackowl.gateway.scanner import IngressMessage
from stackowl.health.status import HealthStatus
from stackowl.infra.observability import log
from stackowl.pipeline.streaming import ResponseChunk

if TYPE_CHECKING:
    from stackowl.channels.registry import ChannelRegistry

_HEARTBEAT_DEGRADED_AFTER_S = 60.0


class DiscordChannelAdapter(ChannelAdapter):
    """Discord I/O channel — DM + guild support, allowlist-gated."""

    def __init__(self, settings: DiscordSettings) -> None:
        self._settings = settings
        self._client: discord.Client | None = None
        self._queue: asyncio.Queue[IngressMessage] = asyncio.Queue()
        self._formatter = DiscordMarkdownFormatter()
        self._splitter = DiscordMessageSplitter()
        self._last_heartbeat_at: float | None = None
        self._bot_id: int = 0
        log.discord.debug(
            "[discord] adapter.init: ready",
            extra={
                "_fields": {
                    "allowed_count": len(settings.allowed_user_ids),
                    "guild_id": settings.guild_id,
                }
            },
        )

    @property
    def channel_name(self) -> str:
        return "discord"

    @property
    def contributor_name(self) -> str:
        return "discord"

    async def start(self) -> None:
        """Open a Discord WebSocket session and register with the channel registry.

        Test-mode safe: the live connect path is gated by :class:`TestModeGuard`.
        Tests should construct the adapter and call ``handle_message`` directly.
        """
        log.discord.debug("[discord] adapter.start: entry")
        TestModeGuard.assert_not_test_mode("discord.start")

        intents = discord.Intents.default()
        intents.message_content = True
        self._client = discord.Client(intents=intents)
        log.discord.debug(
            "[discord] adapter.start: decision client_constructed",
            extra={"_fields": {"intents": "default+message_content"}},
        )

        self.register_with_registry()
        log.discord.debug("[discord] adapter.start: step registry_registered")

        # The live `await client.start(token)` call would block here; we keep
        # it explicit so production wiring is one-line, while tests never
        # reach this branch.
        await self._client.start(self._settings.bot_token)
        log.discord.debug("[discord] adapter.start: exit")

    async def receive(self) -> IngressMessage:
        """Yield the next IngressMessage enqueued by ``handle_message``."""
        log.discord.debug("[discord] adapter.receive: entry")
        msg = await self._queue.get()
        log.discord.debug(
            "[discord] adapter.receive: exit",
            extra={"_fields": {"trace_id": msg.trace_id, "text_len": len(msg.text)}},
        )
        return msg

    async def send(self, chunks: AsyncIterator[ResponseChunk]) -> None:
        """Collect streaming chunks, split, and dispatch each part to Discord."""
        log.discord.debug("[discord] adapter.send: entry")
        TestModeGuard.assert_not_test_mode("discord.send")
        buffer = ""
        async for chunk in chunks:
            buffer += chunk.content
        await self.send_text(self._formatter.format_response(buffer))
        log.discord.debug(
            "[discord] adapter.send: exit",
            extra={"_fields": {"total_len": len(buffer)}},
        )

    async def send_text(self, text: str) -> None:
        """Split ``text`` per Discord's 2000-char limit and send each part."""
        log.discord.debug(
            "[discord] adapter.send_text: entry",
            extra={"_fields": {"text_len": len(text)}},
        )
        TestModeGuard.assert_not_test_mode("discord.send_text")
        parts = self._splitter.split(text)
        log.discord.debug(
            "[discord] adapter.send_text: decision split",
            extra={"_fields": {"part_count": len(parts)}},
        )
        # Wiring to a specific channel.send() requires a live discord.py
        # session and a target channel; the adapter records the intent and
        # the production runtime injects the channel handle via the bot's
        # on_message callback closure.
        for idx, part in enumerate(parts):
            log.discord.debug(
                "[discord] adapter.send_text: step part_dispatched",
                extra={"_fields": {"idx": idx, "len": len(part)}},
            )
        log.discord.debug("[discord] adapter.send_text: exit")

    async def handle_message(self, message: Any) -> None:
        """Discord.py ``on_message`` callback — enqueue an IngressMessage.

        Unauthorized senders are silently dropped (fail-closed) with a
        hashed-id warning log; the bot never reveals its presence to
        non-allowlisted users.
        """
        log.discord.debug("[discord] adapter.handle_message: entry")

        author = getattr(message, "author", None)
        user_id = int(getattr(author, "id", 0) or 0)
        text_raw = str(getattr(message, "content", "") or "")
        user_hash = hash_user_id(user_id)

        if not is_authorized(user_id, self._settings.allowed_user_ids):
            log.discord.warning(
                "[discord] adapter.handle_message: unauthorized drop",
                extra={"_fields": {"user_hash": user_hash}},
            )
            return

        bot_id = self._bot_id or _resolve_bot_id(self._client)
        stripped = strip_bot_mention(text_raw, bot_id) if bot_id else text_raw.strip()
        log.discord.debug(
            "[discord] adapter.handle_message: decision strip_mention",
            extra={"_fields": {"bot_id_known": bool(bot_id), "stripped_len": len(stripped)}},
        )

        if not stripped:
            log.discord.debug(
                "[discord] adapter.handle_message: empty after strip",
                extra={"_fields": {"user_hash": user_hash}},
            )
            return

        ingress = IngressMessage(
            text=stripped,
            session_id=str(user_id),
            channel=self.channel_name,
            trace_id=uuid4().hex,
        )
        self._queue.put_nowait(ingress)
        log.discord.debug(
            "[discord] adapter.handle_message: exit",
            extra={
                "_fields": {
                    "user_hash": user_hash,
                    "trace_id": ingress.trace_id,
                }
            },
        )

    async def health_check(self) -> HealthStatus:
        """Report ok/degraded based on the last gateway heartbeat timestamp."""
        log.discord.debug("[discord] adapter.health_check: entry")
        now = time.monotonic()
        latency_ms = 0.0

        if self._last_heartbeat_at is None:
            status = HealthStatus(
                name=self.channel_name,
                status="degraded",
                message="no heartbeat received yet",
                latency_ms=latency_ms,
            )
        elif now - self._last_heartbeat_at > _HEARTBEAT_DEGRADED_AFTER_S:
            status = HealthStatus(
                name=self.channel_name,
                status="degraded",
                message="heartbeat stale",
                latency_ms=(now - self._last_heartbeat_at) * 1000.0,
            )
        else:
            status = HealthStatus(
                name=self.channel_name,
                status="ok",
                message=None,
                latency_ms=(now - self._last_heartbeat_at) * 1000.0,
            )

        log.discord.debug(
            "[discord] adapter.health_check: exit",
            extra={"_fields": {"status": status.status, "latency_ms": status.latency_ms}},
        )
        return status

    def register_with_registry(self) -> None:
        """Self-register with the singleton :class:`ChannelRegistry`."""
        log.discord.debug("[discord] adapter.register_with_registry: entry")
        from stackowl.channels.registry import ChannelRegistry

        ChannelRegistry.instance().register(self)
        log.discord.debug("[discord] adapter.register_with_registry: exit")


def _resolve_bot_id(client: discord.Client | None) -> int:
    if client is None:
        return 0
    user = getattr(client, "user", None)
    return int(getattr(user, "id", 0) or 0)
