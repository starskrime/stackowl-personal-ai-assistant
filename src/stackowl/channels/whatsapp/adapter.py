"""WhatsAppChannelAdapter — bridges WhatsApp Web to the StackOwl gateway.

Uses Playwright to drive a Chromium browser on WhatsApp Web (self-hosted;
no external cloud APIs required). Message polling runs in a background task
that the adapter cancels on ``stop()``.

Live I/O paths (start, send, browser launch) are guarded by
:class:`TestModeGuard`. Tests call ``handle_message`` directly.

Deferral — memory-promotion via text (C6):
    Memory fact approve/reject is NOT wired for WhatsApp. The other channels
    (Telegram/Slack/Discord) present a memory nudge with an inline approve/reject
    keyboard and dispatch the tap through a button-callback router (``custom_id``).
    WhatsApp is text-only, and — critically — there is no WhatsApp memory-nudge
    *presenter* (no notification path mirrors ``telegram.formatter.format_memory_nudge``),
    so a user is never shown a ``fact_id`` to approve. Wiring a text parser alone
    would be a consumer with no producer. Building it properly requires NEW infra
    (a WhatsApp nudge presenter + notification dispatch + an inbound text-command
    interception seam), which is a feature, not a review fix. Deferred until the
    WhatsApp memory-nudge presentation path is built; at that point add a handler
    mirroring ``DiscordMemoryCallbackHandler`` and dispatch it from the inbound
    loop on language-neutral ``approve <fact_id>`` / ``reject <fact_id>`` tokens.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from typing import Any
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
from stackowl.exceptions import DeliveryError
from stackowl.gateway.scanner import IngressMessage
from stackowl.health.status import HealthStatus
from stackowl.infra.observability import log
from stackowl.pipeline.streaming import ResponseChunk

_POLL_INTERVAL_S = 2.0
_HEARTBEAT_DEGRADED_AFTER_S = 60.0
_DEFAULT_SESSION_SUBDIR = "whatsapp"

# Sentinel distinguishing "no target kwarg passed" (proactive/best-effort →
# logged no-op on miss) from "target explicitly passed" (on-turn → raise on an
# unresolvable miss). ``None`` alone is ambiguous: ``send()`` may pass
# ``target=None`` after narrowing a stray non-str target on the on-turn path,
# which MUST fail loud rather than send to an empty chat (C-1).
_UNSET: Any = object()


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
        # Session→target map (MANDATORY — the session_id is a lossy hash of the
        # JID, so it is NOT itself a send target). Maps session_id ->
        # the raw JID this session's replies route to. ``_last_target`` is the
        # proactive-only fallback, NEVER the primary on-turn path (preserves the
        # concurrent cross-deliver fix).
        self._targets: dict[str, str] = {}
        self._last_target: str | None = None

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

    @property
    def contributor_name(self) -> str:
        """Health-loop contributor name (for healers dict registration)."""
        return "whatsapp"

    def resolve_target(self, session_id: str) -> str | int | None:
        """Resolve the raw JID for ``session_id`` (mirror Slack's _targets map).

        The WhatsApp ``session_id`` is ``whatsapp:{hash_jid(jid)}`` — a LOSSY
        hash, so the map is mandatory: a JID can never be reconstructed from it.
        Returns ``None`` honestly on a miss (never guesses ``_last_target``), so
        the caller records the send as undeliverable rather than cross-delivering.
        """
        target = self._targets.get(session_id)
        log.whatsapp.debug(
            "[whatsapp] adapter.resolve_target: resolved",
            extra={"_fields": {"resolved": target is not None}},
        )
        return target

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

        session_id = f"whatsapp:{hash_jid(jid)}"
        ingress = IngressMessage(
            text=text,
            session_id=session_id,
            channel=self.channel_name,
            trace_id=uuid4().hex,
            # Stamp the raw JID as the per-turn target so this turn replies to ITS
            # OWN chat — never the shared `_last_target` a newer concurrent inbound
            # may overwrite. The session_id is a lossy hash, so the JID itself
            # rides the chunk (resolved back from `_targets` on the proactive path).
            chat_id=jid,
            # ADR-D — a WhatsApp group jid ends with "@g.us"; a 1:1 chat does not.
            # Only the 1:1 chat enables bare-name vocative routing.
            is_direct=not jid.endswith("@g.us"),
        )
        self._queue.put_nowait(ingress)
        # Record the session→JID map + proactive-only fallback.
        self._targets[session_id] = jid
        self._last_target = jid
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
        """Collect streaming chunks, format, and dispatch to WhatsApp.

        Captures the per-turn ``chunk.target`` (the originating JID stamped at
        deliver-time) so this turn replies to ITS OWN chat — not the shared
        ``_last_target`` a newer concurrent inbound may have overwritten. The
        captured JID is passed EXPLICITLY (on-turn) so an unresolvable target
        fails loud instead of sending to an empty chat.
        """
        log.whatsapp.debug("[whatsapp] adapter.send: entry")
        TestModeGuard.assert_not_test_mode("whatsapp.send")
        buffer = ""
        # WhatsApp delivers only to str JIDs; an int target (Telegram chat_id)
        # cannot reach this adapter by construction. Log loudly + narrow to None.
        target: str | None = None
        async for chunk in chunks:
            buffer += chunk.content
            raw = chunk.target
            if isinstance(raw, str):
                target = raw
            elif isinstance(raw, int):
                log.whatsapp.warning(
                    "[whatsapp] adapter.send: unexpected int target — narrowing to None",
                )
                target = None
        log.whatsapp.debug(
            "[whatsapp] adapter.send: decision buffer_ready",
            extra={"_fields": {"total_len": len(buffer), "explicit_target": target is not None}},
        )
        # On-turn: pass the target EXPLICITLY (even None after a stray-type narrow)
        # so an unresolvable target raises rather than sending to an empty chat.
        await self.send_text(self._formatter.format_response(buffer), target=target)
        log.whatsapp.debug("[whatsapp] adapter.send: exit")

    async def send_text(self, text: str, *, target: str | None = _UNSET) -> None:
        """Split text and send each part to the resolved WhatsApp chat (by JID).

        No-target contract (C6 / C-1, see :meth:`ChannelAdapter.send_text`):

        * ``target`` passed EXPLICITLY (the on-turn ``send()`` path) but
          unresolvable → log ``error`` + raise ``DeliveryError("whatsapp",
          "no_target")``. NEVER navigate to an empty chat — an answer to a turn
          is never silently dropped.
        * ``target`` OMITTED (proactive/best-effort) with no ``_last_target`` →
          loud ``error``-level logged NO-OP, never a raise (preserves the
          proactive deliverer never-raises contract).

        4-point logging: entry / decision / step / exit.
        """
        explicit = target is not _UNSET
        resolved = target if explicit else None
        dest = resolved if resolved is not None else self._last_target
        log.whatsapp.debug(
            "[whatsapp] adapter.send_text: entry",
            extra={"_fields": {"text_len": len(text), "explicit": explicit}},
        )
        TestModeGuard.assert_not_test_mode("whatsapp.send_text")
        if dest is None:
            if explicit:
                log.whatsapp.error(
                    "[whatsapp] adapter.send_text: explicit target unresolvable — failing loud",
                )
                raise DeliveryError("whatsapp", "no_target")
            log.whatsapp.error(
                "[whatsapp] adapter.send_text: no target chat (best-effort) — message dropped",
            )
            return
        parts = self._splitter.split(text)
        log.whatsapp.debug(
            "[whatsapp] adapter.send_text: decision split",
            extra={"_fields": {"part_count": len(parts), "jid_hash": hash_jid(dest)}},
        )
        for idx, part in enumerate(parts):
            log.whatsapp.debug(
                "[whatsapp] adapter.send_text: step dispatching",
                extra={"_fields": {"idx": idx, "len": len(part)}},
            )
            # Send to the RESOLVED JID (was a hardcoded empty string — F002). The
            # browser selects the existing chat by full JID (user + group).
            await self._browser.send_message(dest, part)
        log.whatsapp.debug("[whatsapp] adapter.send_text: exit")

    async def send_file(
        self, file_path: str, caption: str | None = None, *, target: str | None = _UNSET
    ) -> None:
        """Send a file to the resolved WhatsApp chat via the attach flow (CHAN-4).

        Destination resolution mirrors :meth:`send_text` (same per-session target
        threading): an EXPLICIT ``target`` JID (the on-turn path) wins; otherwise
        ``_last_target`` (proactive/best-effort). An explicit-but-unresolvable
        target fails loud (``DeliveryError("whatsapp","no_target")``) — a turn's
        file is never silently dropped — while a best-effort send with no target
        is a loud logged no-op (never navigate to an empty chat). ``caption`` is
        sent as the media caption.

        Transport honesty (F-66): a browser attach failure to a RESOLVED chat is
        NOT swallowed — it is logged then re-raised as
        ``DeliveryError("whatsapp", "transport_error")`` so the
        :class:`ProactiveDeliverer` records ``failed`` (instead of a clean send
        while the user never gets the file). "A file send must not crash the
        turn" is still honoured: the DELIVERER catches the raise — that is its
        job — rather than this adapter swallowing it. The attach flow is not
        idempotent, so there is NO retry (a re-attempt risks a duplicate send).
        """
        explicit = target is not _UNSET
        resolved = target if explicit else None
        dest = resolved if resolved is not None else self._last_target
        log.whatsapp.debug(
            "[whatsapp] adapter.send_file: entry",
            extra={"_fields": {"explicit": explicit, "has_caption": bool(caption)}},
        )
        TestModeGuard.assert_not_test_mode("whatsapp.send_file")
        if dest is None:
            if explicit:
                log.whatsapp.error(
                    "[whatsapp] adapter.send_file: explicit target unresolvable — failing loud",
                )
                raise DeliveryError("whatsapp", "no_target")
            log.whatsapp.error(
                "[whatsapp] adapter.send_file: no target chat (best-effort) — file dropped",
            )
            return
        log.whatsapp.debug(
            "[whatsapp] adapter.send_file: step driving attach flow",
            extra={"_fields": {"jid_hash": hash_jid(dest)}},
        )
        try:
            await self._browser.send_file(dest, file_path, caption)
            log.whatsapp.debug("[whatsapp] adapter.send_file: exit sent")
        except Exception as exc:  # F-66 — an on-turn transport failure must surface
            log.whatsapp.error(
                "[whatsapp] adapter.send_file: attach flow failed",
                exc_info=exc,
                extra={"_fields": {"jid_hash": hash_jid(dest)}},
            )
            # The chat resolved but the attach/upload failed — the user never gets
            # the file. Re-raise so the ProactiveDeliverer records ``failed``; the
            # turn stays safe because the DELIVERER catches this, not because we
            # swallow it. The attach flow is not idempotent → no retry.
            raise DeliveryError("whatsapp", "transport_error") from exc

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
        """Report ok/degraded based on transport LIVENESS + the last poll.

        Liveness gate (F004-part1): ``ok`` requires the background poll loop to be
        actually running (``_poll_task`` started and not done) — a poll timestamp
        alone does not prove the browser/poll loop is live. Without it, report
        ``degraded``/``unavailable`` so health never lies about deliverability
        before the channel is started.
        """
        log.whatsapp.debug("[whatsapp] adapter.health_check: entry")
        now = time.monotonic()

        if self._poll_task is None or self._poll_task.done():
            status = HealthStatus(
                name=self.channel_name,
                status="degraded",
                message="poll loop not running — channel not started",
                latency_ms=0.0,
            )
            log.whatsapp.debug(
                "[whatsapp] adapter.health_check: exit",
                extra={"_fields": {"status": status.status, "reason": "no_poll_loop"}},
            )
            return status

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

    # ------------------------------------------------------------------ ADR-6 HealableResource protocol

    @property
    def available(self) -> bool:
        """True if the poll loop is live and ready to send (ADR-6 HealableResource).

        ponytail: bare cached-state read, deliberately unlogged — matches every
        other HealableResource implementer in this codebase (EmbeddingRegistry,
        LanceDBAdapter, KuzuAdapter, DbPool: all bare `available` properties with
        no I/O). Called on every health-sweep tick and from `ensure_available()`
        itself; logging a hot-path property read would be noise, not signal. The
        state-changing path (`ensure_available()`) carries full 4-point logging.
        """
        return self._poll_task is not None and not self._poll_task.done()

    @property
    def unavailable_reason(self) -> str | None:
        """Return the degradation message if unhealthy, else None."""
        # 1. ENTRY — implicit (property access)
        if self.available:
            return None
        # 2. DECISION — derive reason (poll task is None or done)
        log.whatsapp.debug(
            "[whatsapp] adapter.unavailable_reason: exit",
            extra={"_fields": {"reason": "poll loop not running"}},
        )
        # 3. STEP — return the message
        return "poll loop not running — channel not started"

    async def ensure_available(self) -> None:
        """Recover a degraded adapter by restarting the poll loop if needed.

        For WhatsApp, this restarts the poll loop when it has crashed or died.
        """
        # 1. ENTRY
        log.whatsapp.debug(
            "[whatsapp] adapter.ensure_available: entry",
            extra={"_fields": {"available": self.available}},
        )
        # 2. DECISION — no-op if already healthy
        if self.available:
            log.whatsapp.debug(
                "[whatsapp] adapter.ensure_available: already healthy — no-op"
            )
            return
        # 3. STEP — restart the poll loop
        log.whatsapp.debug("[whatsapp] adapter.ensure_available: restarting poll loop")
        self._poll_task = asyncio.create_task(self._poll_loop())
        # 4. EXIT
        log.whatsapp.info(
            "[whatsapp] adapter.ensure_available: exit — poll loop restart attempted"
        )

    def register_on_recycled(self, cb: Callable[[], None]) -> None:
        """No-op: the adapter's state is not cached downstream.

        Every caller re-acquires the adapter via ChannelRegistry or dependency
        injection, so there is no dead ref to clear on recycling. Matches the
        pattern in EmbeddingRegistry and LanceDBAdapter.
        """
        log.whatsapp.debug(
            "[whatsapp] adapter.register_on_recycled: no-op (no downstream dependents)"
        )

    def register_with_registry(self) -> None:
        """Self-register with the singleton ChannelRegistry."""
        log.whatsapp.debug("[whatsapp] adapter.register_with_registry: entry")
        from stackowl.channels.registry import ChannelRegistry

        ChannelRegistry.instance().register(self)
        log.whatsapp.debug("[whatsapp] adapter.register_with_registry: exit")
