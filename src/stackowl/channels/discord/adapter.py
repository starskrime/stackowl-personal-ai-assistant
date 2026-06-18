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
from typing import Any
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
from stackowl.channels.telegram.keyboard import InlineKeyboardBuilder
from stackowl.config.test_mode import TestModeGuard
from stackowl.exceptions import DeliveryError
from stackowl.gateway.scanner import IngressMessage
from stackowl.health.status import HealthStatus
from stackowl.infra.observability import log
from stackowl.pipeline.streaming import ResponseChunk

_HEARTBEAT_DEGRADED_AFTER_S = 60.0

# Sentinel distinguishing "no channel_id kwarg passed" (proactive/best-effort →
# logged no-op on miss) from "channel_id explicitly passed" (on-turn → raise on
# an unresolvable miss). ``None`` alone is ambiguous: ``send()`` may legitimately
# pass ``channel_id=None`` after narrowing a stray non-int target on the on-turn
# path, which MUST fail loud rather than silently drop a turn's answer (C-1).
_UNSET: Any = object()


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
        # Session→target maps (mirror Slack's _targets; the asymmetry is honored
        # by keeping resolution in the adapter that owns the map). ``_targets``
        # maps session_id (== str(user_id)) → the originating channel id; the
        # session_id is NOT itself a send target on Discord (a guild reply must
        # go to message.channel.id, not the user's DM). ``_channels`` holds the
        # live discord.py channel object keyed by channel id — Discord sends via
        # ``channel.send()`` not a raw id, and the object captured off the
        # inbound message is the authoritative handle. The client cache
        # (``get_channel``) is only a fallback for proactive sends to channels
        # not yet seen — it returns None for any uncached channel. Growth is
        # bounded by distinct channels seen (same property as Slack's
        # ``_targets``); an LRU bound is a possible future enhancement.
        # ``_last_channel_id`` is the proactive-only fallback, NEVER the primary
        # path (preserves the concurrent cross-deliver fix).
        self._targets: dict[str, int] = {}
        self._channels: dict[int, Any] = {}
        self._last_channel_id: int | None = None
        # CHAN-4 — inbound attachments cached by their string id so
        # download_media(file_id) can read the bytes via attachment.read()
        # (discord.py has no fetch-attachment-by-id off the client). Bounded by
        # distinct attachments seen on authorized inbound messages.
        self._attachments: dict[str, Any] = {}
        # F005 — the prefix router a tapped button (consent/clarify/memory View)
        # routes its custom_id through. Attached by the orchestrator after the
        # handlers are built (mirrors Telegram's attach_callback_router). None
        # until wired: a tap with no router is a logged no-op (the consent prompt
        # still fails closed on timeout).
        self._callback_router: Any | None = None
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

    def resolve_target(self, session_id: str) -> str | int | None:
        """Resolve the originating channel id for ``session_id`` (mirror Slack).

        The Discord ``session_id`` (== ``str(user_id)``) is NOT itself a send
        target — a guild reply must reach ``message.channel.id``, not the user.
        Reads the adapter-owned ``_targets`` map; returns ``None`` honestly on a
        miss (never guesses ``_last_channel_id``), so the caller records the send
        as undeliverable rather than cross-delivering.
        """
        target = self._targets.get(session_id)
        log.discord.debug(
            "[discord] adapter.resolve_target: resolved",
            extra={"_fields": {"resolved": target is not None}},
        )
        return target

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
        """Collect streaming chunks, split, and dispatch each part to Discord.

        Captures the per-turn ``chunk.target`` (the originating channel id stamped
        at deliver-time) so this turn replies to ITS OWN channel — not the shared
        ``_last_channel_id`` a newer concurrent inbound may have overwritten. The
        captured target is passed EXPLICITLY (on-turn path) to ``send_text`` so an
        unresolvable target fails loud rather than silently dropping the answer.
        """
        log.discord.debug("[discord] adapter.send: entry")
        TestModeGuard.assert_not_test_mode("discord.send")
        buffer = ""
        # Discord delivers only to int channel ids; a str target (Slack) cannot
        # reach this adapter by construction (each turn is delivered by its OWN
        # channel adapter). Log loudly and narrow to None if one ever does.
        target: int | None = None
        async for chunk in chunks:
            buffer += chunk.content
            raw = chunk.target
            if isinstance(raw, str):
                log.discord.warning(
                    "[discord] adapter.send: unexpected str target — narrowing to None",
                )
                target = None
            elif isinstance(raw, int):
                target = raw
        # On-turn path: pass the target EXPLICITLY (even None after a stray-type
        # narrow) so an unresolvable target raises rather than dropping the turn.
        await self.send_text(
            self._formatter.format_response(buffer), channel_id=target
        )
        log.discord.debug(
            "[discord] adapter.send: exit",
            extra={"_fields": {"total_len": len(buffer), "explicit_target": target is not None}},
        )

    async def send_text(self, text: str, *, channel_id: int | None = _UNSET) -> None:
        """Split ``text`` per Discord's 2000-char limit and ``channel.send()`` each part.

        No-target contract (C6 / C-1, see :meth:`ChannelAdapter.send_text`):

        * ``channel_id`` passed EXPLICITLY (the on-turn ``send()`` path) but
          unresolvable → log ``error`` + raise ``DeliveryError("discord",
          "no_target")``. The live channel could not be found for a resolvable
          id → ``DeliveryError("discord", "no_channel")``. An answer to a turn is
          NEVER silently dropped.
        * ``channel_id`` OMITTED (proactive/best-effort) with no
          ``_last_channel_id`` → loud ``error``-level logged NO-OP, never a raise
          (preserves the proactive deliverer never-raises contract).
        """
        explicit = channel_id is not _UNSET
        resolved = channel_id if explicit else None
        target = resolved if resolved is not None else self._last_channel_id
        log.discord.debug(
            "[discord] adapter.send_text: entry",
            extra={"_fields": {"text_len": len(text), "explicit": explicit}},
        )
        TestModeGuard.assert_not_test_mode("discord.send_text")
        if target is None:
            if explicit:
                log.discord.error(
                    "[discord] adapter.send_text: explicit target unresolvable — failing loud",
                )
                raise DeliveryError("discord", "no_target")
            log.discord.error(
                "[discord] adapter.send_text: no target channel (best-effort) — message dropped",
            )
            return
        # Resolve the live channel object — Discord sends via channel.send(), not
        # a raw id. Prefer the per-turn captured handle (the authoritative object
        # received off the inbound message); fall back to the live client cache
        # for channels not yet seen. A still-missing channel (bot kicked /
        # uncached / no client) fails loud (no_channel) rather than silently
        # dropping the answer. Typed Any (as the cache is): get_channel returns a
        # union of channel kinds, only the messageable ones expose .send() — an
        # unmessageable target is a config error caught at send time.
        channel: Any = self._channels.get(target) or (
            self._client.get_channel(target) if self._client is not None else None
        )
        if channel is None:
            log.discord.error(
                "[discord] adapter.send_text: no live channel for target — failing loud",
            )
            raise DeliveryError("discord", "no_channel")
        parts = self._splitter.split(text)
        log.discord.debug(
            "[discord] adapter.send_text: decision split",
            extra={"_fields": {"part_count": len(parts)}},
        )
        for idx, part in enumerate(parts):
            log.discord.debug(
                "[discord] adapter.send_text: step part_dispatched",
                extra={"_fields": {"idx": idx, "len": len(part)}},
            )
            await channel.send(part)
        log.discord.debug("[discord] adapter.send_text: exit")

    async def send_file(
        self, file_path: str, caption: str | None = None, *, channel_id: int | None = _UNSET
    ) -> None:
        """Upload ``file_path`` to a resolved Discord channel (CHAN-4 / F013).

        Destination resolution mirrors :meth:`send_text` (same per-session target
        threading): an EXPLICIT ``channel_id`` (the on-turn path) wins; otherwise
        ``_last_channel_id`` (proactive/best-effort). An explicit-but-unresolvable
        target fails loud (``DeliveryError("discord","no_target")`` / ``no_channel``)
        — a turn's file is never silently dropped — while the best-effort path with
        no target is a loud logged no-op. ``caption`` rides as the message content.

        Self-healing: an upload error is logged and swallowed so a file send never
        crashes the turn (the :class:`ProactiveDeliverer` maps it to ``failed``).
        """
        explicit = channel_id is not _UNSET
        resolved = channel_id if explicit else None
        target = resolved if resolved is not None else self._last_channel_id
        log.discord.debug(
            "[discord] adapter.send_file: entry",
            extra={"_fields": {"explicit": explicit, "has_caption": bool(caption)}},
        )
        TestModeGuard.assert_not_test_mode("discord.send_file")
        if target is None:
            if explicit:
                log.discord.error(
                    "[discord] adapter.send_file: explicit target unresolvable — failing loud",
                )
                raise DeliveryError("discord", "no_target")
            log.discord.error(
                "[discord] adapter.send_file: no target channel (best-effort) — file dropped",
            )
            return
        channel: Any = self._channels.get(target) or (
            self._client.get_channel(target) if self._client is not None else None
        )
        if channel is None:
            log.discord.error(
                "[discord] adapter.send_file: no live channel for target — failing loud",
            )
            raise DeliveryError("discord", "no_channel")
        log.discord.debug(
            "[discord] adapter.send_file: step uploading",
            extra={"_fields": {"channel": target}},
        )
        try:
            discord_file = discord.File(file_path)
            await channel.send(caption or None, file=discord_file)
            log.discord.debug("[discord] adapter.send_file: exit uploaded")
        except Exception as exc:  # self-healing — a file send must not crash the turn
            log.discord.error(
                "[discord] adapter.send_file: upload failed",
                exc_info=exc,
                extra={"_fields": {"channel": target}},
            )

    async def download_media(self, file_id: str) -> bytes:
        """Read an inbound Discord attachment's bytes by its id (CHAN-4 / F013).

        Discord exposes no fetch-attachment-by-id off the client, so inbound
        attachments are cached at :meth:`handle_message` time keyed by their
        string id; this reads the cached attachment via ``attachment.read()``.
        Per the no-hidden-errors rule an unknown id or a read failure is logged
        loudly and re-raised — never a silent empty ``b""``.
        """
        log.discord.debug(
            "[discord] adapter.download_media: entry",
            extra={"_fields": {"file_id_len": len(file_id)}},
        )
        TestModeGuard.assert_not_test_mode("discord.download_media")
        attachment = self._attachments.get(file_id)
        if attachment is None:
            log.discord.error(
                "[discord] adapter.download_media: unknown attachment id",
                extra={"_fields": {"file_id_len": len(file_id)}},
            )
            raise RuntimeError(f"discord download_media: no cached attachment for {file_id!r}")
        try:
            data: bytes = await attachment.read()
        except Exception as exc:
            log.discord.error(
                "[discord] adapter.download_media: attachment.read() failed",
                exc_info=exc,
            )
            raise
        log.discord.debug(
            "[discord] adapter.download_media: exit",
            extra={"_fields": {"byte_len": len(data)}},
        )
        return data

    # ------------------------------------------------------------------ F005 rich

    @property
    def callback_router(self) -> Any | None:
        """The attached button-interaction router (None until wired)."""
        return self._callback_router

    def attach_callback_router(self, router: Any) -> None:
        """Attach the prefix router a tapped View button routes through.

        Mirrors ``TelegramChannelAdapter.attach_callback_router``: the
        orchestrator builds the consent/clarify/memory handlers, registers them on
        a :class:`DiscordCallbackRouter`, then attaches it here so the View
        buttons built by :func:`build_view` can dispatch their custom_id.
        """
        log.discord.debug("[discord] adapter.attach_callback_router: entry")
        self._callback_router = router
        log.discord.debug("[discord] adapter.attach_callback_router: exit")

    async def send_inline_keyboard(
        self,
        text: str,
        keyboard: dict[str, object],
        channel_id: int | None = None,
    ) -> Any:
        """Post ``text`` with an interactive button View to a resolved channel.

        ``channel_id`` targets a specific channel (e.g. the user who initiated a
        consent prompt); when omitted it falls back to ``_last_channel_id``. An
        EXPLICIT channel that cannot be resolved to a live channel raises (the
        consent gate caller fails CLOSED) — the best-effort path (no explicit
        channel) is a logged no-op. Returns the sent ``discord.Message`` so the
        consent gate can later edit it to the chosen decision; ``None`` on the
        best-effort no-target path.
        """
        from stackowl.channels.discord.callbacks import build_view

        explicit = channel_id is not None
        target = channel_id if explicit else self._last_channel_id
        log.discord.debug(
            "[discord] adapter.send_inline_keyboard: entry",
            extra={"_fields": {"text_len": len(text), "explicit": explicit}},
        )
        TestModeGuard.assert_not_test_mode("discord.send_inline_keyboard")
        if target is None:
            log.discord.warning("[discord] adapter.send_inline_keyboard: no target channel")
            if explicit:
                raise DeliveryError("discord", "no_target")
            return None
        channel: Any = self._channels.get(target) or (
            self._client.get_channel(target) if self._client is not None else None
        )
        if channel is None:
            log.discord.error(
                "[discord] adapter.send_inline_keyboard: no live channel — failing loud",
            )
            if explicit:
                raise DeliveryError("discord", "no_channel")
            return None
        view = build_view(keyboard, self)
        log.discord.debug("[discord] adapter.send_inline_keyboard: decision view_built")
        message = await channel.send(text, view=view)
        log.discord.debug("[discord] adapter.send_inline_keyboard: exit")
        return message

    async def edit_message_to_text(self, message: Any, text: str) -> None:
        """Best-effort: rewrite a sent message to ``text`` and drop its buttons.

        Used by the consent gate to render the chosen decision after a tap. The
        message's ``edit`` is real network I/O behind a sync-looking call; any
        failure is logged (the decision is already recorded) — never raises.
        """
        log.discord.debug("[discord] adapter.edit_message_to_text: entry")
        if message is None or not hasattr(message, "edit"):
            log.discord.debug("[discord] adapter.edit_message_to_text: no editable message — skip")
            return
        await message.edit(content=text, view=None)
        log.discord.debug("[discord] adapter.edit_message_to_text: exit")

    async def acknowledge_callback(self, callback_id: str, text: str = "") -> None:
        """No-op ack — discord.py acks interactions via ``interaction.response``.

        The button-callback seam (:func:`build_view`) defers the interaction
        response on tap, so there is no out-of-band ack to perform here. Kept for
        :class:`ChannelAdapter` signature parity.
        """
        log.discord.debug(
            "[discord] adapter.acknowledge_callback: noop (interaction acked at seam)",
            extra={"_fields": {"text_len": len(text)}},
        )

    async def send_clarify(
        self,
        session_id: str,
        question: str,
        choices: tuple[str, ...] | list[str],
        clarify_id: str,
    ) -> None:
        """Deliver a clarify question as tap-buttons (one per choice).

        The Discord destination is resolved from ``resolve_target(session_id)`` —
        the ``str(user_id)`` session_id is NOT itself a send target. Each non-blank
        choice becomes a button whose ``custom_id`` is ``clarify:{clarify_id}:{idx}``,
        PRESERVING each choice's ORIGINAL index across blanks (so the tap maps to
        ``entry.choices[idx]`` even with blanks present — mirrors Telegram/Slack).

        Self-heals to the base numbered-text fallback on any error: an unresolved
        target, no choices, or a delivery failure all degrade rather than crash
        the turn (the gateway treats ``send_clarify`` as best-effort).
        """
        n_nonblank = sum(1 for c in choices if str(c).strip())
        log.discord.debug(
            "[discord] adapter.send_clarify: entry",
            extra={"_fields": {"n_choices": n_nonblank, "clarify_id": clarify_id}},
        )
        dest = self.resolve_target(session_id)
        if not isinstance(dest, int) or not n_nonblank:
            log.discord.debug(
                "[discord] adapter.send_clarify: decision base_text_fallback",
                extra={"_fields": {"has_target": isinstance(dest, int), "n": n_nonblank}},
            )
            await super().send_clarify(session_id, question, choices, clarify_id)
            return
        try:
            builder = InlineKeyboardBuilder()
            n_buttons = 0
            for idx, choice in enumerate(choices):
                c = str(choice).strip()
                if not c:
                    continue
                builder.add_button(c, f"clarify:{clarify_id}:{idx}")
                n_buttons += 1
            keyboard = builder.build()
            log.discord.debug(
                "[discord] adapter.send_clarify: step keyboard_built",
                extra={"_fields": {"n_buttons": n_buttons}},
            )
            await self.send_inline_keyboard(question, keyboard, channel_id=dest)
        except Exception as exc:  # self-healing — any failure → base numbered text
            log.discord.error(
                "[discord] adapter.send_clarify: button delivery failed — text fallback",
                exc_info=exc,
                extra={"_fields": {"clarify_id": clarify_id}},
            )
            await super().send_clarify(session_id, question, choices, clarify_id)
            return
        log.discord.debug("[discord] adapter.send_clarify: exit")

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

        # Stamp the ORIGINATING channel id (NOT author.id): a guild reply must go
        # back to message.channel.id, else it misroutes to the user's DM or
        # nowhere. session_id stays str(user_id) for memory/identity.
        channel = getattr(message, "channel", None)
        channel_id = int(getattr(channel, "id", 0) or 0)
        ingress = IngressMessage(
            text=stripped,
            session_id=str(user_id),
            channel=self.channel_name,
            trace_id=uuid4().hex,
            chat_id=channel_id,
        )
        self._queue.put_nowait(ingress)
        # Record the session→channel-id map + the live channel handle so the
        # reply turn can resolve where to send. The captured channel object is
        # the authoritative send handle (the client cache is only a fallback).
        # ``_last_channel_id`` is the proactive-only fallback, NEVER the primary
        # on-turn path.
        if channel_id:
            self._targets[str(user_id)] = channel_id
            if channel is not None:
                self._channels[channel_id] = channel
            self._last_channel_id = channel_id
        # CHAN-4 — cache any inbound attachments by string id so download_media
        # can later read their bytes (discord.py has no fetch-by-id off client).
        for att in getattr(message, "attachments", None) or []:
            att_id = getattr(att, "id", None)
            if att_id is not None:
                self._attachments[str(att_id)] = att
        log.discord.debug(
            "[discord] adapter.handle_message: exit",
            extra={
                "_fields": {
                    "user_hash": user_hash,
                    "trace_id": ingress.trace_id,
                    "has_channel": channel_id != 0,
                }
            },
        )

    async def health_check(self) -> HealthStatus:
        """Report ok/degraded based on transport LIVENESS + the last heartbeat.

        Liveness gate (F004-part1): ``ok`` requires a live ``_client`` — a fresh
        heartbeat alone does not prove send capability (there is no transport to
        send through until startup constructs the client). Without it, report
        ``degraded`` so health never lies about deliverability before the channel
        is wired.
        """
        log.discord.debug("[discord] adapter.health_check: entry")
        now = time.monotonic()
        latency_ms = 0.0

        if self._client is None:
            status = HealthStatus(
                name=self.channel_name,
                status="degraded",
                message="no live client — channel not started",
                latency_ms=latency_ms,
            )
            log.discord.debug(
                "[discord] adapter.health_check: exit",
                extra={"_fields": {"status": status.status, "reason": "no_client"}},
            )
            return status

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
