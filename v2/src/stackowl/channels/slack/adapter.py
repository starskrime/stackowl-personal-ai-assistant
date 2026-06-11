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
from typing import TYPE_CHECKING, Any

from stackowl.channels.base import ChannelAdapter
from stackowl.channels.splitter import SlackMessageSplitter
from stackowl.config.test_mode import TestModeGuard
from stackowl.gateway.scanner import IngressMessage
from stackowl.health.status import HealthStatus
from stackowl.infra.observability import log
from stackowl.pipeline.streaming import ResponseChunk

from .helpers import (
    ActionsBlock,
    ButtonElement,
    PlainText,
    SectionBlock,
    hash_user_id,
    is_authorized,
    strip_bot_mention,
)
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

    async def send_file(
        self, file_path: str, caption: str | None = None, *, target: str | None = None
    ) -> None:
        """Upload ``file_path`` to a Slack channel via ``files_upload_v2``.

        The destination is resolved EXACTLY as :meth:`send_text` does: an explicit
        ``target`` (the per-message channel id) wins, otherwise ``self._last_target``
        (single-terminal/back-compat). The per-channel ``thread_ts`` (if any) threads
        the upload under the originating message — a channel reply threads, a DM
        uploads to the channel root. ``caption`` becomes Slack's ``initial_comment``.

        Self-healing: a missing client or an unresolved destination is a logged
        no-op (the :class:`ProactiveDeliverer` maps undeliverable to ``failed``);
        any upload error is logged and swallowed so a file send never crashes the
        turn. ``TestModeGuard`` blocks live I/O in tests.
        """
        dest = target if target is not None else self._last_target
        thread_ts = self._threads.get(dest) if dest is not None else None
        if thread_ts is None and dest == self._last_target:
            thread_ts = self._last_thread
        log.slack.debug(
            "[slack] adapter.send_file: entry",
            extra={
                "_fields": {
                    "explicit_target": target is not None,
                    "has_caption": bool(caption),
                    "threaded": thread_ts is not None,
                }
            },
        )
        TestModeGuard.assert_not_test_mode("slack.send_file")
        client = self._client()
        if client is None or dest is None:
            log.slack.warning(
                "[slack] adapter.send_file: no client/target — file dropped",
                extra={
                    "_fields": {"has_client": client is not None, "has_dest": dest is not None}
                },
            )
            return
        upload_kwargs: dict[str, object] = {
            "channel": dest,
            "file": file_path,
            "initial_comment": caption or None,
        }
        if thread_ts is not None:
            upload_kwargs["thread_ts"] = thread_ts
        log.slack.debug(
            "[slack] adapter.send_file: step upload",
            extra={"_fields": {"channel": dest, "threaded": thread_ts is not None}},
        )
        try:
            await client.files_upload_v2(**upload_kwargs)
            log.slack.debug(
                "[slack] adapter.send_file: exit uploaded",
                extra={"_fields": {"channel": dest}},
            )
        except Exception as exc:  # self-healing — a file send must not crash the turn
            log.slack.error(
                "[slack] adapter.send_file: upload failed",
                exc_info=exc,
                extra={"_fields": {"channel": dest}},
            )

    async def download_media(self, file_id: str) -> bytes:
        """Download an inbound Slack file's bytes by its file id.

        Slack inbound files carry a ``url_private`` reachable only with the bot
        token. The Bolt client doesn't expose a raw byte-fetch, so we resolve the
        ``url_private`` via ``files_info(file=file_id)`` then perform an authorized
        ``GET`` (``Authorization: Bearer <bot_token>``) with ``httpx`` (already a
        project dependency). Per the no-hidden-errors rule a genuine download
        failure (no client, missing url, network/HTTP error) is logged loudly and
        re-raised — never a silent empty ``b""``.
        """
        log.slack.debug(
            "[slack] adapter.download_media: entry",
            extra={"_fields": {"file_id_len": len(file_id)}},
        )
        TestModeGuard.assert_not_test_mode("slack.download_media")
        client = self._client()
        if client is None:
            log.slack.error(
                "[slack] adapter.download_media: no Bolt client — cannot download",
                extra={"_fields": {"file_id_len": len(file_id)}},
            )
            raise RuntimeError("slack download_media: no Bolt client attached")
        try:
            log.slack.debug("[slack] adapter.download_media: step files_info")
            info = await client.files_info(file=file_id)
            file_obj = info.get("file") if isinstance(info, dict) else None
            url_private = (
                file_obj.get("url_private") if isinstance(file_obj, dict) else None
            )
            if not isinstance(url_private, str) or not url_private:
                log.slack.error(
                    "[slack] adapter.download_media: files_info returned no url_private",
                    extra={"_fields": {"file_id_len": len(file_id)}},
                )
                raise RuntimeError("slack download_media: no url_private for file")
            log.slack.debug(
                "[slack] adapter.download_media: step authorized GET",
                extra={"_fields": {"has_url": True}},
            )
            import httpx

            headers = {"Authorization": f"Bearer {self._settings.bot_token}"}
            async with httpx.AsyncClient() as http:
                resp = await http.get(url_private, headers=headers)
                resp.raise_for_status()
                data = bytes(resp.content)
        except Exception as exc:
            log.slack.error(
                "[slack] adapter.download_media: fetch failed",
                exc_info=exc,
                extra={"_fields": {"file_id_len": len(file_id)}},
            )
            raise
        log.slack.debug(
            "[slack] adapter.download_media: exit",
            extra={"_fields": {"bytes_len": len(data)}},
        )
        return data

    async def send_clarify(
        self,
        session_id: str,
        question: str,
        choices: tuple[str, ...] | list[str],
        clarify_id: str,
    ) -> None:
        """Deliver a clarify question as tap-buttons (one Block Kit button/choice).

        The Slack destination is resolved from ``target_for_session(session_id)``
        — the ``slack:{hash}`` session_id is NOT itself a send target. When that
        returns ``None`` we degrade to the numbered-text fallback (best-effort to
        ``_last_target``) rather than guessing a channel.

        Each non-blank choice becomes a button whose ``action_id``/``value`` is
        ``clarify:{clarify_id}:{idx}`` — a tap is routed to
        :class:`~stackowl.channels.slack.clarify.SlackClarifyResolver`, which maps
        ``idx`` back to the choice text and wakes the parked turn. The ORIGINAL
        index is PRESERVED even when earlier choices are blank (mirrors Telegram),
        so ``clarify:{id}:{idx}`` always indexes the gateway's stored
        ``entry.choices[idx]``. Open-ended questions (no non-blank choices) post
        as plain text and are answered by typing.

        Self-healing: an unresolved target, a missing client, or any delivery
        error degrades to a best-effort numbered-text post — a delivery failure
        must never crash the turn (the gateway treats ``send_clarify`` as
        best-effort).
        """
        n_nonblank = sum(1 for c in choices if str(c).strip())
        log.slack.debug(
            "[slack] adapter.send_clarify: entry",
            extra={"_fields": {"n_choices": n_nonblank, "clarify_id": clarify_id}},
        )
        # 2. DECISION — resolve the Slack destination from the session→channel map.
        dest = self.target_for_session(session_id)
        if dest is None:
            log.slack.warning(
                "[slack] adapter.send_clarify: no target for session — text fallback",
                extra={"_fields": {"session_id": session_id, "clarify_id": clarify_id}},
            )
            await self._send_clarify_text_fallback(question, choices, channel=None)
            return

        if not n_nonblank:
            # Open-ended question — answered by typing (no buttons).
            log.slack.debug(
                "[slack] adapter.send_clarify: decision no_choices — plain text",
                extra={"_fields": {"channel": dest}},
            )
            await self._send_clarify_text_fallback(question, choices, channel=dest)
            return

        client = self._client()
        if client is None:
            log.slack.warning(
                "[slack] adapter.send_clarify: no Bolt client — text fallback",
                extra={"_fields": {"clarify_id": clarify_id}},
            )
            await self._send_clarify_text_fallback(question, choices, channel=dest)
            return

        try:
            buttons: list[ButtonElement] = []
            for idx, choice in enumerate(choices):
                label = str(choice).strip()
                if not label:
                    continue
                action = f"clarify:{clarify_id}:{idx}"
                buttons.append(
                    ButtonElement(
                        text=PlainText(text=label),
                        action_id=action,
                        value=action,
                    )
                )
            blocks = [
                SectionBlock(text=PlainText(text=question)).model_dump(),
                ActionsBlock(elements=buttons).model_dump(),
            ]
            log.slack.debug(
                "[slack] adapter.send_clarify: step blocks_built",
                extra={"_fields": {"channel": dest, "n_buttons": len(buttons)}},
            )
            post_kwargs: dict[str, object] = {
                "channel": dest,
                "text": question,
                "blocks": blocks,
            }
            thread_ts = self._threads.get(dest)
            if thread_ts is not None:
                post_kwargs["thread_ts"] = thread_ts
            await client.chat_postMessage(**post_kwargs)
        except Exception as exc:  # self-healing — any failure → best-effort text
            log.slack.error(
                "[slack] adapter.send_clarify: button delivery failed — text fallback",
                exc_info=exc,
                extra={"_fields": {"channel": dest, "clarify_id": clarify_id}},
            )
            await self._send_clarify_text_fallback(question, choices, channel=dest)
            return
        log.slack.debug(
            "[slack] adapter.send_clarify: exit",
            extra={"_fields": {"channel": dest, "delivered": True}},
        )

    async def _send_clarify_text_fallback(
        self,
        question: str,
        choices: tuple[str, ...] | list[str],
        *,
        channel: str | None,
    ) -> None:
        """Best-effort numbered-text delivery of a clarify question (never raises).

        Renders ``question`` followed by a numbered list of the non-blank choices
        (a glyph-free, language-neutral ``N. choice`` cadence). Posts directly via
        the Bolt client so it works regardless of TestModeGuard; ``channel=None``
        falls back to ``_last_target`` (single-terminal/back-compat path).
        """
        dest = channel if channel is not None else self._last_target
        lines = [question]
        n = 0
        for choice in choices:
            label = str(choice).strip()
            if not label:
                continue
            n += 1
            lines.append(f"{n}. {label}")
        body = "\n".join(lines)
        try:
            client = self._client()
            if client is None or dest is None:
                log.slack.warning(
                    "[slack] adapter.send_clarify: text fallback has no target/client — dropped",
                    extra={"_fields": {"has_client": client is not None, "has_dest": dest is not None}},
                )
                return
            post_kwargs: dict[str, object] = {"channel": dest, "text": body}
            thread_ts = self._threads.get(dest)
            if thread_ts is not None:
                post_kwargs["thread_ts"] = thread_ts
            await client.chat_postMessage(**post_kwargs)
            log.slack.debug(
                "[slack] adapter.send_clarify: text fallback posted",
                extra={"_fields": {"channel": dest, "n_choices": n}},
            )
        except Exception as exc:  # truly best-effort — never raise into the turn
            log.slack.error(
                "[slack] adapter.send_clarify: text fallback post failed",
                exc_info=exc,
                extra={"_fields": {"channel": dest}},
            )

    async def acknowledge_callback(self, callback_id: str, text: str = "") -> None:
        """No-op for Slack — block_actions are ack'd per-handler by Bolt's ``ack()``.

        Slack requires the ``block_actions`` HTTP handler to call ``ack()`` within
        3 seconds; that acknowledgement is owned by the Bolt ``@app.action``
        handler (B3), NOT by an out-of-band call here. This method exists to honor
        the channel-adapter contract shared with Telegram (whose
        ``acknowledge_callback`` answers a callback_query) so cross-channel callers
        (e.g. the memory handlers) stay uniform; on Slack it is a documented no-op.
        """
        log.slack.debug(
            "[slack] adapter.acknowledge_callback: no-op (ack owned by Bolt handler)",
            extra={"_fields": {"callback_id_len": len(callback_id), "has_text": bool(text)}},
        )

    async def _chat_update(
        self,
        channel: str,
        ts: str,
        text: str,
        blocks: list[dict[str, object]] | None = None,
    ) -> bool:
        """Best-effort rewrite of an existing message (drops buttons by default).

        Used to turn an interactive prompt into a resolved decision after a tap so
        it can't be re-tapped. Fail-open: any Bolt failure is LOGGED and returns
        ``False`` rather than raising — a failed cosmetic update must never break a
        decision that is already recorded.
        """
        log.slack.debug(
            "[slack] adapter._chat_update: entry",
            extra={"_fields": {"channel": channel, "text_len": len(text)}},
        )
        client = self._client()
        if client is None:
            log.slack.warning("[slack] adapter._chat_update: no client — skipped")
            return False
        update_kwargs: dict[str, object] = {"channel": channel, "ts": ts, "text": text}
        if blocks is not None:
            update_kwargs["blocks"] = blocks
        try:
            await client.chat_update(**update_kwargs)
            log.slack.debug("[slack] adapter._chat_update: exit updated")
            return True
        except Exception as exc:  # fail-open — decision already recorded
            log.slack.error(
                "[slack] adapter._chat_update: update failed",
                exc_info=exc,
                extra={"_fields": {"channel": channel}},
            )
            return False

    def _client(self) -> Any | None:
        """Return the live Bolt client (``app.client``), or None when unattached.

        Typed ``Any`` so the dynamically-shaped Bolt client (``chat_postMessage`` /
        ``chat_update``) stays callable without importing slack_sdk at module load
        — mirrors the consent prompter's ``_client`` seam.
        """
        app = self._app
        if app is None:
            return None
        return getattr(app, "client", None)

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

    def register_with_registry(self, registry: ChannelRegistry | None = None) -> None:
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
