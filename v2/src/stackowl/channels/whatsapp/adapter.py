"""WhatsAppChannelAdapter — bridges WhatsApp Web to the StackOwl gateway.

Uses Playwright to drive a Chromium browser on WhatsApp Web (self-hosted;
no external cloud APIs required). Message polling runs in a background task
that the adapter cancels on ``stop()``.

Live I/O paths (start, send, browser launch) are guarded by
:class:`TestModeGuard`. Tests call ``handle_message`` directly.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING
from uuid import uuid4

from stackowl.channels.base import ChannelAdapter
from stackowl.channels.splitter import WhatsAppMessageSplitter
from stackowl.channels.whatsapp.browser import WhatsAppBrowserDriver
from stackowl.channels.whatsapp.helpers import (
    WhatsAppMarkdownFormatter,
    hash_jid,
    is_authorized,
)
from stackowl.channels.whatsapp.session import WhatsAppSessionManager
from stackowl.channels.whatsapp.settings import WhatsAppSettings
from stackowl.config.test_mode import TestModeGuard
from stackowl.gateway.scanner import IngressMessage
from stackowl.health.status import HealthStatus
from stackowl.infra.observability import log
from stackowl.pipeline.streaming import ResponseChunk

if TYPE_CHECKING:
    from stackowl.channels.registry import ChannelRegistry

_POLL_INTERVAL_S = 2.0
_HEARTBEAT_DEGRADED_AFTER_S = 60.0
_DEFAULT_SESSION_SUBDIR = "whatsapp"


class WhatsAppChannelAdapter(ChannelAdapter):
    """WhatsApp I/O channel — Playwright-powered WhatsApp Web, allowlist-gated."""

    def __init__(
        self,
        settings: WhatsAppSettings,
        data_dir: str = "data",
    ) -> None:
        self._settings = settings

        session_dir = settings.session_dir or f"{data_dir}/{_DEFAULT_SESSION_SUBDIR}"
        self._session_manager = WhatsAppSessionManager(session_dir)
        self._browser = WhatsAppBrowserDriver(settings, self._session_manager)

        self._queue: asyncio.Queue[IngressMessage] = asyncio.Queue()
        self._formatter = WhatsAppMarkdownFormatter()
        self._splitter = WhatsAppMessageSplitter()
        self._poll_task: asyncio.Task[None] | None = None
        self._last_poll_at: float | None = None

        log.whatsapp.debug(
            "[whatsapp] adapter.init: ready",
            extra={
                "_fields": {
                    "allowed_count": len(settings.allowed_phone_numbers),
                    "session_dir": session_dir,
                    "headless": settings.headless,
                }
            },
        )

    @property
    def channel_name(self) -> str:
        return "whatsapp"

    async def start(self) -> None:
        """Launch browser, start poll loop, and register with channel registry.

        Test-mode safe: the live browser launch path is gated by TestModeGuard.
        Tests should construct the adapter and call ``handle_message`` directly.
        """
        log.whatsapp.debug("[whatsapp] adapter.start: entry")
        TestModeGuard.assert_not_test_mode("whatsapp.start")

        await self._browser.start()
        log.whatsapp.debug("[whatsapp] adapter.start: step browser_started")

        self._poll_task = asyncio.create_task(self._poll_loop())
        log.whatsapp.debug("[whatsapp] adapter.start: step poll_loop_started")

        self.register_with_registry()
        log.whatsapp.debug("[whatsapp] adapter.start: exit")

    async def _poll_loop(self) -> None:
        """Background loop: poll for messages every POLL_INTERVAL_S seconds.

        Never crashes the loop — exceptions are logged and the loop continues.
        """
        log.whatsapp.debug("[whatsapp] adapter._poll_loop: started")
        while True:
            try:
                messages = await self._browser.poll_messages()
                for msg_dict in messages:
                    jid = str(msg_dict.get("jid") or "")
                    text = str(msg_dict.get("text") or "")
                    if jid and text:
                        await self.handle_message(jid, text)
            except asyncio.CancelledError:
                log.whatsapp.debug("[whatsapp] adapter._poll_loop: cancelled — exiting")
                return
            except Exception as exc:
                log.whatsapp.error(
                    "[whatsapp] adapter._poll_loop: unhandled error",
                    exc_info=exc,
                )
            await asyncio.sleep(_POLL_INTERVAL_S)

    async def handle_message(self, jid: str, text: str) -> None:
        """Validate sender, create IngressMessage, and enqueue it.

        Unauthorized senders are silently dropped (fail-closed). The JID is
        never logged raw — only a sha256[:8] hash is used.

        4-point logging: entry / decision / step / exit.
        """
        log.whatsapp.debug(
            "[whatsapp] adapter.handle_message: entry",
            extra={"_fields": {"jid_hash": hash_jid(jid)}},
        )

        if not is_authorized(jid, self._settings.allowed_phone_numbers):
            log.whatsapp.warning(
                "[whatsapp] adapter.handle_message: unauthorized — silently dropped",
                extra={"_fields": {"jid_hash": hash_jid(jid)}},
            )
            return

        log.whatsapp.debug(
            "[whatsapp] adapter.handle_message: decision authorized",
            extra={"_fields": {"jid_hash": hash_jid(jid), "text_len": len(text)}},
        )

        ingress = IngressMessage(
            text=text,
            session_id=f"whatsapp:{hash_jid(jid)}",
            channel=self.channel_name,
            trace_id=uuid4().hex,
        )
        self._queue.put_nowait(ingress)
        self._last_poll_at = time.monotonic()

        log.whatsapp.debug(
            "[whatsapp] adapter.handle_message: exit",
            extra={
                "_fields": {
                    "jid_hash": hash_jid(jid),
                    "trace_id": ingress.trace_id,
                }
            },
        )

    async def receive(self) -> IngressMessage:
        """Yield the next IngressMessage enqueued by ``handle_message``."""
        log.whatsapp.debug("[whatsapp] adapter.receive: entry")
        msg = await self._queue.get()
        log.whatsapp.debug(
            "[whatsapp] adapter.receive: exit",
            extra={"_fields": {"trace_id": msg.trace_id, "text_len": len(msg.text)}},
        )
        return msg

    async def send(self, chunks: AsyncIterator[ResponseChunk]) -> None:
        """Collect streaming chunks, format, and dispatch to WhatsApp."""
        log.whatsapp.debug("[whatsapp] adapter.send: entry")
        TestModeGuard.assert_not_test_mode("whatsapp.send")
        buffer = ""
        async for chunk in chunks:
            buffer += chunk.content
        log.whatsapp.debug(
            "[whatsapp] adapter.send: decision buffer_ready",
            extra={"_fields": {"total_len": len(buffer)}},
        )
        await self.send_text(self._formatter.format_response(buffer))
        log.whatsapp.debug("[whatsapp] adapter.send: exit")

    async def send_text(self, text: str) -> None:
        """Split text and send each chunk to the active WhatsApp chat.

        4-point logging: entry / decision / step / exit.
        """
        log.whatsapp.debug(
            "[whatsapp] adapter.send_text: entry",
            extra={"_fields": {"text_len": len(text)}},
        )
        TestModeGuard.assert_not_test_mode("whatsapp.send_text")
        parts = self._splitter.split(text)
        log.whatsapp.debug(
            "[whatsapp] adapter.send_text: decision split",
            extra={"_fields": {"part_count": len(parts)}},
        )
        for idx, part in enumerate(parts):
            log.whatsapp.debug(
                "[whatsapp] adapter.send_text: step dispatching",
                extra={"_fields": {"idx": idx, "len": len(part)}},
            )
            # NOTE: send_message requires a JID; the adapter must be wired to
            # the active session JID by the caller (e.g. via a context variable
            # set when the inbound message arrived). This is a known limitation
            # of the Playwright-driven approach — we send to the current open chat.
            await self._browser.send_message("", part)
        log.whatsapp.debug("[whatsapp] adapter.send_text: exit")

    async def stop(self) -> None:
        """Cancel the poll loop task and shut down the browser.

        4-point logging: entry / decision / step / exit.
        """
        log.whatsapp.debug("[whatsapp] adapter.stop: entry")
        if self._poll_task is not None and not self._poll_task.done():
            log.whatsapp.debug("[whatsapp] adapter.stop: decision cancelling_poll_task")
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                log.whatsapp.error(
                    "[whatsapp] adapter.stop: poll_task raised on cancel",
                    exc_info=exc,
                )
        log.whatsapp.debug("[whatsapp] adapter.stop: step stopping_browser")
        await self._browser.stop()
        log.whatsapp.debug("[whatsapp] adapter.stop: exit")

    async def health_check(self) -> HealthStatus:
        """Report ok/degraded based on the last successful poll timestamp."""
        log.whatsapp.debug("[whatsapp] adapter.health_check: entry")
        now = time.monotonic()

        if self._last_poll_at is None:
            status = HealthStatus(
                name=self.channel_name,
                status="degraded",
                message="no messages polled yet",
                latency_ms=0.0,
            )
        elif now - self._last_poll_at > _HEARTBEAT_DEGRADED_AFTER_S:
            status = HealthStatus(
                name=self.channel_name,
                status="degraded",
                message="poll heartbeat stale",
                latency_ms=(now - self._last_poll_at) * 1000.0,
            )
        else:
            status = HealthStatus(
                name=self.channel_name,
                status="ok",
                message=None,
                latency_ms=(now - self._last_poll_at) * 1000.0,
            )

        log.whatsapp.debug(
            "[whatsapp] adapter.health_check: exit",
            extra={"_fields": {"status": status.status}},
        )
        return status

    def register_with_registry(self) -> None:
        """Self-register with the singleton ChannelRegistry."""
        log.whatsapp.debug("[whatsapp] adapter.register_with_registry: entry")
        from stackowl.channels.registry import ChannelRegistry

        ChannelRegistry.instance().register(self)
        log.whatsapp.debug("[whatsapp] adapter.register_with_registry: exit")
