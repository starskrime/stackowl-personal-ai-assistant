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
        # Session→target map (load-bearing): session_id → the Slack channel id
        # that this session's replies route to. Phase B's consent + clarify
        # resolve the Slack destination from THIS map because the session_id
        # (``slack:{hash}``) is NOT itself a send target. ``_last_target`` is the
        # single-terminal fallback when a chunk carries no explicit target.
        self._targets: dict[str, str] = {}
        self._last_target: str | None = None
        # Parallel thread map: session_id → thread_ts. A reply to a channel
        # message threads under the originating thread; a DM (no thread) replies
        # to the channel directly. chunk.target carries only the channel id (a
        # simple routing string) — the thread_ts is resolved adapter-side at
        # send time, keyed by the same id used as the target.
        self._threads: dict[str, str] = {}
        self._last_thread: str | None = None
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

    def target_for_session(self, session_id: str) -> str | None:
        """Resolve the Slack send destination (channel id) for a session.

        Phase B (consent / clarify) calls this to find where to deliver an
        out-of-band prompt, since the ``slack:{hash}`` session_id is not itself
        a send target. Returns ``None`` for an unknown session.
        """
        return self._targets.get(session_id)

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
        # Per-turn delivery target: a turn's chunks all carry the SAME `target`
        # (the originating channel id stamped at deliver-time). Capture it so this
        # turn replies to ITS OWN channel, not the shared `_last_target` (which a
        # newer concurrent inbound event may have overwritten). None →
        # send_text falls back to `_last_target` (single-terminal/back-compat).
        target: str | None = None
        async for chunk in chunks:
            buffer += chunk.content
            raw = chunk.target
            if isinstance(raw, str):
                target = raw
            elif isinstance(raw, int):
                # Slack only ever delivers str channel-id targets; an int
                # (Telegram chat_id) cannot reach the Slack adapter by
                # construction (each turn is delivered by its OWN channel
                # adapter). Log loudly, then fall back to `_last_target`.
                log.slack.warning(
                    "[slack] adapter.send: unexpected int target — falling back to _last_target",
                    extra={"_fields": {"target": raw}},
                )
                target = None
        log.slack.debug(
            "[slack] adapter.send: decision collected",
            extra={"_fields": {"total_len": len(buffer), "explicit_target": target is not None}},
        )
        await self.send_text(buffer, target=target)
        log.slack.debug(
            "[slack] adapter.send: exit",
            extra={"_fields": {"explicit_target": target is not None}},
        )

    async def send_text(self, text: str, *, target: str | None = None) -> None:
        """Split ``text`` per Slack's limit and post each part to ``target``.

        ``target`` is a Slack channel id (the per-message destination threaded
        from ``IngressMessage.chat_id`` → ``ResponseChunk.target``); when omitted
        it falls back to ``self._last_target`` for back-compat callers (proactive
        deliverer, clarify degrade-path). The thread_ts (if any) is resolved
        adapter-side from the channel id so a channel reply threads correctly and
        a DM reply goes to the channel directly.
        """
        TestModeGuard.assert_not_test_mode("slack.send_text")
        dest = target if target is not None else self._last_target
        # Resolve the originating thread for this channel: a per-channel thread
        # map populated in handle_event. None → reply to the channel (DM path).
        thread_ts = self._threads.get(dest) if dest is not None else None
        if thread_ts is None and dest == self._last_target:
            thread_ts = self._last_thread
        log.slack.debug(
            "[slack] adapter.send_text: entry",
            extra={
                "_fields": {
                    "text_len": len(text),
                    "explicit_target": target is not None,
                    "threaded": thread_ts is not None,
                }
            },
        )
        if dest is None:
            log.slack.warning(
                "[slack] adapter.send_text: no target channel — message dropped",
                extra={"_fields": {"text_len": len(text)}},
            )
            return
        parts = self._splitter.split(text)
        for idx, part in enumerate(parts):
            await self._post_text(part, index=idx, channel=dest, thread_ts=thread_ts)
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
        session_id = f"slack:{hash_user_id(user_id)}"
        trace_id = f"slack-{hash_user_id(user_id)}-{self._session_counter}"

        # Resolve the Slack send destination. The event carries `channel` (the
        # routing key) and optionally `thread_ts`/`ts`. The chunk.target is kept
        # a SIMPLE channel-id string; the thread_ts is resolved adapter-side at
        # send time. So a channel message threads its reply under the originating
        # thread, while a DM (no thread_ts) replies to the channel directly.
        channel_id = str(event.get("channel", ""))
        raw_thread = event.get("thread_ts") or event.get("ts")
        thread_ts = str(raw_thread) if raw_thread is not None else None
        log.slack.debug(
            "[slack] adapter.handle_event: decision target_resolved",
            extra={
                "_fields": {
                    "channel": channel_id,
                    "threaded": thread_ts is not None,
                }
            },
        )

        msg = IngressMessage(
            text=cleaned,
            session_id=session_id,
            channel=self.channel_name,
            trace_id=trace_id,
            # Stamp the routing channel id so this turn delivers back to ITS OWN
            # channel — never the shared `_last_target`, which a newer concurrent
            # inbound event may overwrite before this turn finishes.
            chat_id=channel_id,
        )
        # Record the session→target map (Phase B) + the per-channel thread map +
        # single-terminal fallbacks, all consulted at send time.
        self._targets[session_id] = channel_id
        self._last_target = channel_id
        if thread_ts is not None:
            self._threads[channel_id] = thread_ts
        else:
            self._threads.pop(channel_id, None)
        self._last_thread = thread_ts
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

    async def _post_text(
        self,
        text: str,
        *,
        index: int,
        channel: str,
        thread_ts: str | None = None,
    ) -> None:
        """Send a single message part via the attached Bolt app, if any.

        ``channel`` is the resolved Slack destination (channel id) — replacing
        the former hardcoded ``"@stackowl"`` so replies reach the originating
        conversation. ``thread_ts`` (when set) threads the reply under the
        originating message; when ``None`` the reply posts to the channel root
        (DM path).

        When no app is attached (unit test path) we only log — the
        TestModeGuard above already protected against accidental live I/O.
        """
        msg_id = uuid.uuid4().hex[:12]
        log.slack.debug(
            "[slack] adapter._post_text: entry",
            extra={
                "_fields": {
                    "msg_id": msg_id,
                    "part_index": index,
                    "len": len(text),
                    "channel": channel,
                    "threaded": thread_ts is not None,
                }
            },
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
        post_kwargs: dict[str, object] = {"channel": channel, "text": text}
        if thread_ts is not None:
            post_kwargs["thread_ts"] = thread_ts
        try:
            await client.chat_postMessage(**post_kwargs)
            log.slack.debug(
                "[slack] adapter._post_text: exit posted",
                extra={"_fields": {"msg_id": msg_id, "channel": channel}},
            )
        except Exception as err:  # noqa: BLE001
            log.slack.error(
                "[slack] adapter._post_text: post failed",
                exc_info=err,
                extra={"_fields": {"msg_id": msg_id}},
            )
