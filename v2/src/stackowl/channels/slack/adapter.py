"""SlackChannelAdapter — Slack Bolt-backed channel adapter for StackOwl.

The adapter does NOT open the live Socket Mode / HTTP connection itself; the
production runner (or an integration test harness) is responsible for wiring
the underlying :class:`slack_bolt.async_app.AsyncApp` to ``handle_event`` and
``handle_slash_command``. This keeps the adapter unit-testable without
network I/O while preserving a clean integration seam.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from stackowl.channels.base import ChannelAdapter
from stackowl.channels.splitter import SlackMessageSplitter
from stackowl.config.test_mode import TestModeGuard
from stackowl.gateway.scanner import IngressMessage
from stackowl.health.status import HealthStatus
from stackowl.infra.observability import log
from stackowl.pipeline.streaming import ResponseChunk

from .helpers import hash_user_id, is_authorized, strip_bot_mention
from .settings import SlackSettings

if TYPE_CHECKING:
    from stackowl.channels.registry import ChannelRegistry


_HEALTH_STALE_AFTER_S = 90.0


class SlackChannelAdapter(ChannelAdapter):
    """Slack channel adapter — see module docstring for the integration contract."""

    contributor_name: str = "slack_channel"

    def __init__(self, settings: SlackSettings) -> None:
        log.slack.debug(
            "[slack] adapter.init: entry",
            extra={
                "_fields": {
                    "socket_mode": settings.socket_mode,
                    "allowed_count": len(settings.allowed_user_ids),
                }
            },
        )
        self._settings = settings
        self._queue: asyncio.Queue[IngressMessage] = asyncio.Queue()
        self._splitter = SlackMessageSplitter()
        self._bot_user_id: str = ""
        self._session_counter = 0
        self._last_ping_at: datetime | None = None
        # The live AsyncApp is injected by the integration runner — we keep an
        # untyped reference so the adapter remains importable without
        # slack_bolt being on the path at import time.
        self._app: object | None = None
        log.slack.info(
            "[slack] adapter.init: exit",
            extra={"_fields": {"channel": self.channel_name}},
        )

    # ------------------------------------------------------------------ #
    # ChannelAdapter contract
    # ------------------------------------------------------------------ #

    @property
    def channel_name(self) -> str:
        return "slack"

    async def start(self) -> None:
        """Prepare the adapter for live use (no network I/O happens here)."""
        TestModeGuard.assert_not_test_mode("slack.start")
        log.slack.debug(
            "[slack] adapter.start: entry",
            extra={"_fields": {"socket_mode": self._settings.socket_mode}},
        )
        mode = "socket_mode" if self._settings.socket_mode else "http_webhook"
        log.slack.info(
            "[slack] adapter.start: would connect",
            extra={"_fields": {"mode": mode}},
        )
        # The production runner attaches `_app` after import (via
        # `set_bolt_app`) and calls AsyncApp.start_async() itself.
        log.slack.debug("[slack] adapter.start: exit")

    async def receive(self) -> IngressMessage:
        msg = await self._queue.get()
        log.slack.debug(
            "[slack] adapter.receive: yielded message",
            extra={
                "_fields": {
                    "session_id": msg.session_id,
                    "trace_id": msg.trace_id,
                    "text_len": len(msg.text),
                }
            },
        )
        return msg

    async def send(self, chunks: AsyncIterator[ResponseChunk]) -> None:
        TestModeGuard.assert_not_test_mode("slack.send")
        log.slack.debug("[slack] adapter.send: entry")
        buffer = ""
        async for chunk in chunks:
            buffer += chunk.content
        log.slack.debug(
            "[slack] adapter.send: decision collected",
            extra={"_fields": {"total_len": len(buffer)}},
        )
        parts = self._splitter.split(buffer)
        log.slack.debug(
            "[slack] adapter.send: step split",
            extra={"_fields": {"part_count": len(parts)}},
        )
        for idx, part in enumerate(parts):
            await self._post_text(part, index=idx)
        log.slack.debug(
            "[slack] adapter.send: exit",
            extra={"_fields": {"part_count": len(parts)}},
        )

    async def send_text(self, text: str) -> None:
        TestModeGuard.assert_not_test_mode("slack.send_text")
        log.slack.debug(
            "[slack] adapter.send_text: entry",
            extra={"_fields": {"text_len": len(text)}},
        )
        parts = self._splitter.split(text)
        for idx, part in enumerate(parts):
            await self._post_text(part, index=idx)
        log.slack.debug(
            "[slack] adapter.send_text: exit",
            extra={"_fields": {"part_count": len(parts)}},
        )

    # ------------------------------------------------------------------ #
    # Integration hooks called by the live AsyncApp
    # ------------------------------------------------------------------ #

    def set_bot_user_id(self, bot_user_id: str) -> None:
        """Record the bot's own user ID so mentions can be stripped."""
        log.slack.debug(
            "[slack] adapter.set_bot_user_id: entry",
            extra={"_fields": {"bot_id_present": bool(bot_user_id)}},
        )
        self._bot_user_id = bot_user_id

    def set_bolt_app(self, app: object) -> None:
        """Attach a live ``slack_bolt.async_app.AsyncApp`` instance."""
        log.slack.debug(
            "[slack] adapter.set_bolt_app: entry",
            extra={"_fields": {"has_app": app is not None}},
        )
        self._app = app

    def mark_ping(self) -> None:
        """Called by the connection heartbeat — keeps health_check honest."""
        self._last_ping_at = datetime.now(tz=UTC)

    async def handle_event(
        self, event: dict[str, object], user_id: str, text: str
    ) -> None:
        """Filter and enqueue an inbound Slack event.

        Unauthorized senders are dropped silently (fail-closed) with a warning
        log that records only the sha256 hash of the user ID. Bot self-mentions
        are stripped before the message reaches the gateway router.
        """
        log.slack.debug(
            "[slack] adapter.handle_event: entry",
            extra={
                "_fields": {
                    "user_hash": hash_user_id(user_id),
                    "text_len": len(text),
                    "event_type": str(event.get("type", "")),
                }
            },
        )
        if not is_authorized(user_id, self._settings.allowed_user_ids):
            log.slack.warning(
                "[slack] adapter.handle_event: dropping unauthorized sender",
                extra={"_fields": {"user_hash": hash_user_id(user_id)}},
            )
            return

        cleaned = strip_bot_mention(text, self._bot_user_id) if self._bot_user_id else text.strip()
        log.slack.debug(
            "[slack] adapter.handle_event: decision cleaned",
            extra={"_fields": {"cleaned_len": len(cleaned)}},
        )

        self._session_counter += 1
        trace_id = f"slack-{hash_user_id(user_id)}-{self._session_counter}"
        msg = IngressMessage(
            text=cleaned,
            session_id=f"slack:{hash_user_id(user_id)}",
            channel=self.channel_name,
            trace_id=trace_id,
        )
        await self._queue.put(msg)
        log.slack.debug(
            "[slack] adapter.handle_event: exit — queued",
            extra={"_fields": {"trace_id": trace_id, "queue_size": self._queue.qsize()}},
        )

    async def health_check(self) -> HealthStatus:
        """Return ok if a recent ping arrived, otherwise degraded."""
        start = time.perf_counter()
        last = self._last_ping_at
        if last is None:
            latency = (time.perf_counter() - start) * 1000.0
            return HealthStatus(
                name=self.contributor_name,
                status="degraded",
                message="no Slack ping recorded yet",
                latency_ms=latency,
            )
        age_s = (datetime.now(tz=UTC) - last).total_seconds()
        latency = (time.perf_counter() - start) * 1000.0
        if age_s > _HEALTH_STALE_AFTER_S:
            return HealthStatus(
                name=self.contributor_name,
                status="degraded",
                message=f"last ping {age_s:.0f}s ago",
                latency_ms=latency,
            )
        return HealthStatus(
            name=self.contributor_name,
            status="ok",
            message=f"connected — last ping {age_s:.0f}s ago",
            latency_ms=latency,
        )

    def register_with_registry(self, registry: "ChannelRegistry | None" = None) -> None:
        """Register this adapter with the singleton :class:`ChannelRegistry`."""
        from stackowl.channels.registry import ChannelRegistry

        target = registry or ChannelRegistry.instance()
        log.slack.debug(
            "[slack] adapter.register_with_registry: entry",
            extra={"_fields": {"channel": self.channel_name}},
        )
        target.register(self)
        log.slack.info(
            "[slack] adapter.register_with_registry: exit",
            extra={"_fields": {"channel": self.channel_name}},
        )

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    async def _post_text(self, text: str, *, index: int) -> None:
        """Send a single message part via the attached Bolt app, if any.

        When no app is attached (unit test path) we only log — the
        TestModeGuard above already protected against accidental live I/O.
        """
        msg_id = uuid.uuid4().hex[:12]
        log.slack.debug(
            "[slack] adapter._post_text: entry",
            extra={"_fields": {"msg_id": msg_id, "part_index": index, "len": len(text)}},
        )
        if self._app is None:
            log.slack.debug(
                "[slack] adapter._post_text: no Bolt app attached — skip",
                extra={"_fields": {"msg_id": msg_id}},
            )
            return
        # The live AsyncApp exposes `.client.chat_postMessage(...)`. We avoid
        # importing slack_sdk types here so the unit tests don't need the
        # package installed; the production runner is responsible for wiring.
        client = getattr(self._app, "client", None)
        if client is None:
            log.slack.warning(
                "[slack] adapter._post_text: attached app has no client",
                extra={"_fields": {"msg_id": msg_id}},
            )
            return
        try:
            await client.chat_postMessage(channel="@stackowl", text=text)
            log.slack.debug(
                "[slack] adapter._post_text: exit posted",
                extra={"_fields": {"msg_id": msg_id}},
            )
        except Exception as err:  # noqa: BLE001
            log.slack.error(
                "[slack] adapter._post_text: post failed",
                exc_info=err,
                extra={"_fields": {"msg_id": msg_id}},
            )
