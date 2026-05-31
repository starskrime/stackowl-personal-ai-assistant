"""TelegramChannelAdapter — bridges the Telegram Bot API to the StackOwl gateway.

The adapter consumes a :class:`TelegramSettings` injected by the caller,
exposes the canonical :class:`ChannelAdapter` surface, and self-registers
with :class:`ChannelRegistry` on ``start()``.

Live I/O paths are guarded by :class:`TestModeGuard` so tests never open a
long-poll connection. Message intake is mediated by an internal
``asyncio.Queue`` that :meth:`_handle_update` populates from python-telegram-bot
callbacks.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from telegram import Update
from telegram.ext import CallbackQueryHandler, ContextTypes, MessageHandler, filters

from stackowl.channels.base import ChannelAdapter
from stackowl.channels.splitter import TelegramMessageSplitter
from stackowl.channels.telegram._bot import build_inline_keyboard, start_bot, stop_bot
from stackowl.channels.telegram.formatter import TelegramMarkdownFormatter
from stackowl.channels.telegram.helpers import hash_user_id, is_authorized, strip_bot_mention, strip_command_bot_suffix
from stackowl.channels.telegram.settings import TelegramSettings
from stackowl.config.test_mode import TestModeGuard
from stackowl.gateway.scanner import IngressMessage
from stackowl.health.status import HealthStatus
from stackowl.infra.observability import log
from stackowl.pipeline.streaming import ResponseChunk

if TYPE_CHECKING:
    pass

_UPDATE_DEGRADED_AFTER_S = 120.0


class TelegramChannelAdapter(ChannelAdapter):
    """Telegram I/O channel — DM + group support, allowlist-gated."""

    def __init__(self, settings: TelegramSettings) -> None:
        self._settings = settings
        self._queue: asyncio.Queue[IngressMessage] = asyncio.Queue()
        self._formatter = TelegramMarkdownFormatter()
        self._splitter = TelegramMessageSplitter()
        self._bot_app: Any = None
        self._last_update_at: float | None = None
        self._last_chat_id: int | None = None
        self._bot_user_id: int = 0
        self._bot_username: str = ""
        log.telegram.debug(
            "[telegram] adapter.init: ready",
            extra={
                "_fields": {
                    "allowed_count": len(settings.allowed_user_ids),
                    "webhook_mode": settings.webhook_url is not None,
                }
            },
        )

    @property
    def channel_name(self) -> str:
        return "telegram"

    @property
    def contributor_name(self) -> str:
        return "telegram"

    async def start(self) -> None:
        """Open a Telegram session and register with the channel registry."""
        log.telegram.debug("[telegram] adapter.start: entry")
        TestModeGuard.assert_not_test_mode("telegram.start")

        app, bot_id, bot_username = await start_bot(
            self._settings.bot_token,
            self._settings.webhook_url,
            self._settings.webhook_secret,
        )
        self._bot_app = app
        self._bot_user_id = bot_id
        self._bot_username = bot_username

        app.add_handler(MessageHandler(filters.TEXT, self._handle_update))
        self.register_with_registry()
        # RC-D: publish the slash-command menu so "/" autocompletes in clients.
        from stackowl.channels.telegram.commands_registration import register_commands
        from stackowl.commands.registry import CommandRegistry

        await register_commands(app.bot, CommandRegistry.instance().list())
        log.telegram.debug("[telegram] adapter.start: exit")

    async def stop(self) -> None:
        """Gracefully shut down the Telegram session."""
        log.telegram.debug("[telegram] adapter.stop: entry")
        await stop_bot(self._bot_app)
        log.telegram.debug("[telegram] adapter.stop: exit")

    async def receive(self) -> IngressMessage:
        """Yield the next IngressMessage enqueued by ``_handle_update``."""
        log.telegram.info("[telegram] adapter.receive: entry")
        msg = await self._queue.get()
        log.telegram.info(
            "[telegram] adapter.receive: exit",
            extra={"_fields": {"trace_id": msg.trace_id, "text_len": len(msg.text)}},
        )
        return msg

    async def send(self, chunks: AsyncIterator[ResponseChunk]) -> None:
        """Collect streaming chunks, format, and dispatch to Telegram."""
        log.telegram.info("[telegram] adapter.send: entry")
        TestModeGuard.assert_not_test_mode("telegram.send")
        buffer = ""
        async for chunk in chunks:
            buffer += chunk.content
        await self.send_text(self._formatter.format_response(buffer))
        log.telegram.info(
            "[telegram] adapter.send: exit",
            extra={"_fields": {"total_len": len(buffer)}},
        )

    async def send_text(self, text: str) -> None:
        """Split ``text`` per Telegram's limit and send each part."""
        log.telegram.debug(
            "[telegram] adapter.send_text: entry",
            extra={"_fields": {"text_len": len(text)}},
        )
        TestModeGuard.assert_not_test_mode("telegram.send_text")
        if self._bot_app is None or self._last_chat_id is None:
            log.telegram.warning(
                "[telegram] adapter.send_text: no active chat — message dropped",
                extra={"_fields": {"has_app": self._bot_app is not None}},
            )
            return
        parts = self._splitter.split(text)
        log.telegram.debug(
            "[telegram] adapter.send_text: decision split",
            extra={"_fields": {"part_count": len(parts)}},
        )
        for idx, part in enumerate(parts):
            log.telegram.debug(
                "[telegram] adapter.send_text: step part_dispatched",
                extra={"_fields": {"idx": idx, "len": len(part)}},
            )
            await self._bot_app.bot.send_message(
                chat_id=self._last_chat_id,
                text=part,
                parse_mode="MarkdownV2",
            )
        log.telegram.debug("[telegram] adapter.send_text: exit")

    async def send_inline_keyboard(
        self, text: str, keyboard: dict[str, object], chat_id: int | None = None
    ) -> None:
        """Send a message with an inline keyboard attachment.

        ``chat_id`` targets a specific chat (e.g. the user who initiated a consent
        prompt); when omitted it falls back to the most-recent chat. Raises
        :class:`RuntimeError` when no target chat can be resolved so callers that
        require delivery (consent gate) can fail closed immediately.
        """
        log.telegram.debug(
            "[telegram] adapter.send_inline_keyboard: entry",
            extra={"_fields": {"text_len": len(text), "explicit_chat": chat_id is not None}},
        )
        TestModeGuard.assert_not_test_mode("telegram.send_inline_keyboard")
        target_chat = chat_id if chat_id is not None else self._last_chat_id
        if self._bot_app is None or target_chat is None:
            log.telegram.warning("[telegram] adapter.send_inline_keyboard: no target chat")
            # An explicit chat_id (e.g. the consent gate) requires delivery — raise
            # so the caller fails closed instead of silently hanging. The best-effort
            # path (no explicit chat_id, e.g. notifications) stays a silent no-op.
            if chat_id is not None:
                raise RuntimeError("telegram.send_inline_keyboard: target chat unavailable")
            return
        markup = build_inline_keyboard(keyboard)
        log.telegram.debug(
            "[telegram] adapter.send_inline_keyboard: decision markup_built",
            extra={"_fields": {"has_markup": markup is not None}},
        )
        await self._bot_app.bot.send_message(
            chat_id=target_chat,
            text=text,
            parse_mode="MarkdownV2",
            reply_markup=markup,
        )
        log.telegram.debug("[telegram] adapter.send_inline_keyboard: exit")

    async def send_clarify(
        self,
        session_id: str,
        question: str,
        choices: tuple[str, ...] | list[str],
        clarify_id: str,
    ) -> None:
        """Deliver a clarify question as tap-buttons (one per choice).

        Targets the asking user's chat (``session_id`` == Telegram user id). Each
        choice becomes an inline button whose ``callback_data`` is
        ``clarify:{clarify_id}:{idx}`` — a tap is resolved by the clarify callback
        handler, which maps ``idx`` back to the choice text and wakes the parked
        turn. Open-ended questions (no choices) are sent as plain text and
        answered by typing.

        Self-healing: a non-int ``session_id``, a callback_data overflow, or any
        delivery error degrades to a best-effort ``send_text`` of the bare
        question — delivery failure must never crash the turn (the gateway treats
        ``send_clarify`` as best-effort).
        """
        # Count non-blank choices for the "no choices at all" decision, but DO NOT
        # renumber them: button callback_data must carry each choice's ORIGINAL
        # index so `clarify:{id}:{idx}` always indexes the gateway's stored
        # `entry.choices[idx]`. A re-filtered list would desync the indices the
        # moment any choice is blank, mapping a tap to the wrong choice text.
        n_nonblank = sum(1 for c in choices if str(c).strip())
        # The question is free-form (often LLM) text; send_text/send_inline_keyboard
        # send with parse_mode=MarkdownV2 and assume PRE-ESCAPED bodies (codebase
        # convention). Escape here so a lone '.'/'('/'-'/'_' can't make Telegram
        # reject the send and silently leave the turn parked. Button LABELS are NOT
        # markdown-parsed by Telegram, so choices stay raw.
        body = self._formatter.format_plain(question)
        log.telegram.debug(
            "[telegram] adapter.send_clarify: entry",
            extra={"_fields": {"n_choices": n_nonblank, "clarify_id": clarify_id}},
        )
        try:
            chat_id = int(session_id)
        except (TypeError, ValueError):
            log.telegram.error(
                "[telegram] adapter.send_clarify: session_id is not a chat id — text fallback",
                extra={"_fields": {"session_id": session_id, "clarify_id": clarify_id}},
            )
            await self._send_clarify_text_fallback(body)
            return

        if not n_nonblank:
            # Open-ended question (no non-blank choices) — answered by typing.
            log.telegram.debug(
                "[telegram] adapter.send_clarify: decision no_choices — plain text",
                extra={"_fields": {"chat_id": chat_id}},
            )
            await self._send_clarify_text_fallback(body, chat_id=chat_id)
            return

        try:
            from stackowl.channels.telegram.keyboard import InlineKeyboardBuilder

            builder = InlineKeyboardBuilder()
            # Enumerate the ORIGINAL choices and PRESERVE each original index in the
            # callback_data, skipping blanks. So `clarify:{id}:{idx}` always indexes
            # the gateway's stored `entry.choices[idx]` even with blanks present.
            n_buttons = 0
            for idx, choice in enumerate(choices):
                c = str(choice).strip()
                if not c:
                    continue
                builder.add_button(c, f"clarify:{clarify_id}:{idx}")
                n_buttons += 1
            keyboard = builder.build()
            log.telegram.debug(
                "[telegram] adapter.send_clarify: step keyboard_built",
                extra={"_fields": {"chat_id": chat_id, "n_buttons": n_buttons}},
            )
            await self.send_inline_keyboard(body, keyboard, chat_id=chat_id)
        except Exception as exc:  # self-healing — any failure → best-effort text
            log.telegram.error(
                "[telegram] adapter.send_clarify: button delivery failed — text fallback",
                exc_info=exc,
                extra={"_fields": {"chat_id": chat_id, "clarify_id": clarify_id}},
            )
            await self._send_clarify_text_fallback(body, chat_id=chat_id)
            return
        log.telegram.debug(
            "[telegram] adapter.send_clarify: exit",
            extra={"_fields": {"chat_id": chat_id, "delivered": True}},
        )

    async def _send_clarify_text_fallback(
        self, question: str, *, chat_id: int | None = None
    ) -> None:
        """Best-effort plain-text delivery of a clarify question (never raises)."""
        try:
            if chat_id is not None:
                # Pin delivery to the asking user's chat even on the text path.
                prior = self._last_chat_id
                self._last_chat_id = chat_id
                try:
                    await self.send_text(question)
                finally:
                    self._last_chat_id = prior
            else:
                await self.send_text(question)
        except Exception as exc:  # delivery failure must not crash the turn
            log.telegram.error(
                "[telegram] adapter.send_clarify: text fallback failed",
                exc_info=exc,
                extra={"_fields": {"chat_id": chat_id}},
            )

    async def download_media(self, file_id: str) -> bytes:
        """Download a media file by its Telegram file_id."""
        log.telegram.debug(
            "[telegram] adapter.download_media: entry",
            extra={"_fields": {"file_id_len": len(file_id)}},
        )
        TestModeGuard.assert_not_test_mode("telegram.download_media")
        if self._bot_app is None:
            log.telegram.warning("[telegram] adapter.download_media: bot not initialised")
            return b""
        log.telegram.debug("[telegram] adapter.download_media: decision get_file")
        tg_file = await self._bot_app.bot.get_file(file_id)
        data = bytes(await tg_file.download_as_bytearray())
        log.telegram.debug(
            "[telegram] adapter.download_media: exit",
            extra={"_fields": {"bytes_len": len(data)}},
        )
        return data

    async def acknowledge_callback(self, callback_id: str, text: str = "") -> None:
        """Answer a Telegram callback query (required within 15 seconds)."""
        log.telegram.debug(
            "[telegram] adapter.acknowledge_callback: entry",
            extra={"_fields": {"callback_id_len": len(callback_id)}},
        )
        TestModeGuard.assert_not_test_mode("telegram.acknowledge_callback")
        if self._bot_app is None:
            log.telegram.warning("[telegram] adapter.acknowledge_callback: bot not initialised")
            return
        log.telegram.debug("[telegram] adapter.acknowledge_callback: decision answer_query")
        await self._bot_app.bot.answer_callback_query(callback_id, text=text or None)
        log.telegram.debug("[telegram] adapter.acknowledge_callback: exit")

    def attach_callback_router(self, router: Any) -> None:
        """Route Telegram callback-query taps (inline buttons) through ``router``.

        ``router`` must expose an async ``route(update, context)`` callback. Used
        to wire the consent inline-keyboard round-trip; safe no-op if the bot is
        not initialised.
        """
        log.telegram.debug("[telegram] adapter.attach_callback_router: entry")
        if self._bot_app is None:
            log.telegram.warning("[telegram] adapter.attach_callback_router: bot not initialised — skipped")
            return
        self._bot_app.add_handler(CallbackQueryHandler(router.route))
        log.telegram.debug("[telegram] adapter.attach_callback_router: exit")

    async def _handle_update(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """python-telegram-bot callback — enqueue an IngressMessage (fail-closed)."""
        log.telegram.info("[telegram] adapter.handle_update: entry")
        message = update.effective_message
        if message is None:
            log.telegram.debug("[telegram] adapter.handle_update: no effective_message — skip")
            return
        user = update.effective_user
        user_id = int(user.id) if user is not None else 0
        user_hash = hash_user_id(user_id)
        if not is_authorized(user_id, self._settings.allowed_user_ids):
            log.telegram.warning(
                "[telegram] adapter.handle_update: unauthorized drop",
                extra={"_fields": {"user_hash": user_hash}},
            )
            return
        text_raw = message.text or ""
        stripped = (
            strip_bot_mention(text_raw, self._bot_username)
            if self._bot_username
            else text_raw.strip()
        )
        stripped = strip_command_bot_suffix(stripped, self._bot_username)
        log.telegram.debug(
            "[telegram] adapter.handle_update: decision strip_mention",
            extra={"_fields": {"stripped_len": len(stripped)}},
        )
        if not stripped:
            log.telegram.debug("[telegram] adapter.handle_update: empty after strip — skip")
            return
        chat = update.effective_chat
        chat_id = int(chat.id) if chat is not None else 0
        ingress = IngressMessage(
            text=stripped,
            session_id=str(user_id),
            channel=self.channel_name,
            trace_id=uuid4().hex,
        )
        self._queue.put_nowait(ingress)
        self._last_update_at = time.monotonic()
        self._last_chat_id = chat_id
        log.telegram.info(
            "[telegram] adapter.handle_update: exit",
            extra={"_fields": {"user_hash": user_hash, "trace_id": ingress.trace_id}},
        )

    async def health_check(self) -> HealthStatus:
        """Report ok/degraded based on the last received update timestamp."""
        log.telegram.debug("[telegram] adapter.health_check: entry")
        now = time.monotonic()
        if self._last_update_at is None:
            status = HealthStatus(
                name=self.channel_name, status="degraded",
                message="no update received yet", latency_ms=0.0,
            )
        elif now - self._last_update_at > _UPDATE_DEGRADED_AFTER_S:
            status = HealthStatus(
                name=self.channel_name, status="degraded",
                message="update stream stale",
                latency_ms=(now - self._last_update_at) * 1000.0,
            )
        else:
            status = HealthStatus(
                name=self.channel_name, status="ok", message=None,
                latency_ms=(now - self._last_update_at) * 1000.0,
            )
        log.telegram.debug(
            "[telegram] adapter.health_check: exit",
            extra={"_fields": {"status": status.status}},
        )
        return status

    def register_with_registry(self) -> None:
        """Self-register with the singleton :class:`ChannelRegistry`."""
        log.telegram.debug("[telegram] adapter.register_with_registry: entry")
        from stackowl.channels.registry import ChannelRegistry
        ChannelRegistry.instance().register(self)
        log.telegram.debug("[telegram] adapter.register_with_registry: exit")
