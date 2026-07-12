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
import contextlib
import time
from collections.abc import AsyncIterator, Callable
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

from telegram import Update
from telegram.constants import ChatAction
from telegram.error import BadRequest
from telegram.ext import CallbackQueryHandler, ContextTypes, MessageHandler, filters

from stackowl.channels.base import ChannelAdapter
from stackowl.channels.splitter import TelegramMessageSplitter
from stackowl.channels.telegram._bot import (
    build_inline_keyboard,
    restart_polling,
    start_bot,
    stop_bot,
)
from stackowl.channels.telegram.formatter import TelegramMarkdownFormatter
from stackowl.channels.telegram.helpers import hash_user_id, is_authorized, strip_bot_mention, strip_command_bot_suffix
from stackowl.channels.telegram.progress_render import TelegramProgressView
from stackowl.channels.telegram.settings import TelegramSettings
from stackowl.config.progress_settings import ProgressSettings
from stackowl.config.test_mode import TestModeGuard
from stackowl.exceptions import DeliveryError
from stackowl.gateway.scanner import IngressMessage
from stackowl.health.status import HealthStatus
from stackowl.infra.observability import log, traced_span
from stackowl.pipeline.streaming import ResponseChunk

if TYPE_CHECKING:
    from telegram import Message

    from stackowl.channels.liveness import ChannelLivenessStore
    from stackowl.channels.telegram.voice import TelegramVoiceHandler
    from stackowl.commands.response import Action

_UPDATE_DEGRADED_AFTER_S = 120.0

# How often the gateway stamps the cross-process receive-liveness row (PB0b/RC0).
# The core health sweep flags degraded after 4 missed beats (_STALE_AFTER_S=120).
HEARTBEAT_INTERVAL_S = 30.0

# Sentinel distinguishing "no chat_id kwarg passed" (proactive/best-effort →
# logged no-op on miss) from "chat_id explicitly passed" (on-turn → raise on an
# unresolvable miss). ``None`` alone is ambiguous: ``send()`` may pass
# ``chat_id=None`` after narrowing a stray non-int target on the on-turn path,
# which MUST fail loud rather than silently drop a turn's answer (C6 / C-1).
_UNSET: Any = object()


def _mint_request_id() -> str:
    """Mint a unique, non-empty request_id (= trace_id) for an ingress message.

    ``uuid4().hex`` is probabilistically unique; the guard rejects an empty
    id so a collision can't reintroduce cross-delivery once routing keys on
    request_id.
    """
    rid = uuid4().hex
    if not rid:
        log.gateway.error("[mint] telegram request_id empty")
        raise ValueError("empty request_id")
    return rid


class TelegramChannelAdapter(ChannelAdapter):
    """Telegram I/O channel — DM + group support, allowlist-gated."""

    def __init__(
        self,
        settings: TelegramSettings,
        *,
        progress: ProgressSettings | None = None,
        liveness: ChannelLivenessStore | None = None,
    ) -> None:
        self._settings = settings
        self._progress = progress or ProgressSettings()
        self._queue: asyncio.Queue[IngressMessage] = asyncio.Queue()
        self._formatter = TelegramMarkdownFormatter()
        self._splitter = TelegramMessageSplitter()
        self._bot_app: Any = None
        # PB0b/RC0 — cross-process receive-liveness. When present, start() spawns a
        # heartbeat that stamps the shared DB while the updater is polling, so the
        # core health sweep can tell "quiet-but-alive" from "dead".
        self._liveness = liveness
        self._liveness_task: asyncio.Task[None] | None = None
        # PB0c/RC0 — bounds gateway-local self-heal to ONE restart per outage
        # episode. Set True before a restart attempt; reset to False on any tick
        # that observes updater.running (the episode is over).
        self._recovery_attempted = False
        self._last_update_at: float | None = None
        self._last_chat_id: int | None = None
        self._bot_user_id: int = 0
        self._bot_username: str = ""
        # Optional voice-transcription handler (set by the orchestrator only when
        # transcription is enabled). None → no filters.VOICE handler is registered
        # in start(), so behavior is byte-identical to a build without the feature.
        self._voice_handler: TelegramVoiceHandler | None = None
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

    def resolve_target(self, session_id: str) -> str | int | None:
        """Resolve the numeric chat id for ``session_id`` (private-chat convention).

        Mirrors :func:`stackowl.notifications.router_helpers.resolve_target_chat_id`
        for the telegram channel: a private chat's ``session_id`` IS the chat id
        (session_id == str(user_id) == chat_id). A non-numeric session (e.g. a
        group, whose chat_id != user_id) cannot be resolved here and returns
        ``None`` — logged, never guessed — so the caller records the send as
        undeliverable rather than riding ``_last_chat_id``.
        """
        log.telegram.debug(
            "[telegram] adapter.resolve_target: entry",
            extra={"_fields": {"session_present": bool(session_id)}},
        )
        sid = (session_id or "").strip()
        if not sid:
            log.telegram.warning(
                "[telegram] adapter.resolve_target: blank session_id — unresolved",
            )
            return None
        try:
            chat_id = int(sid)
        except ValueError:
            log.telegram.warning(
                "[telegram] adapter.resolve_target: session_id is not a chat id — unresolved",
                extra={"_fields": {"session_id": sid}},
            )
            return None
        log.telegram.debug(
            "[telegram] adapter.resolve_target: exit",
            extra={"_fields": {"chat_id": chat_id}},
        )
        return chat_id

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
        # Voice messages → transcribe → confirm (only when a handler was wired).
        if self._voice_handler is not None:
            app.add_handler(MessageHandler(filters.VOICE, self._voice_handler.handle_voice))
            log.telegram.debug("[telegram] adapter.start: voice handler registered")
        self.register_with_registry()
        # RC-D: publish the slash-command menu so "/" autocompletes in clients.
        from stackowl.channels.telegram.commands_registration import register_commands
        from stackowl.commands.registry import CommandRegistry

        await register_commands(app.bot, CommandRegistry.instance().list())
        # PB0b/RC0 — start the cross-process receive-liveness heartbeat. Seed the
        # row once immediately so the sweep never sees a transient "down" at boot,
        # then stamp every HEARTBEAT_INTERVAL_S while the updater is polling.
        if self._liveness is not None:
            await self._liveness.mark_alive(self.channel_name)
            self._liveness_task = asyncio.create_task(self._liveness_heartbeat())
            self._liveness_task.add_done_callback(self._on_liveness_task_done)
            log.telegram.debug("[telegram] adapter.start: liveness heartbeat started")
        log.telegram.debug("[telegram] adapter.start: exit")

    async def _beat_once(self) -> None:
        """One heartbeat tick: stamp liveness when alive, else self-heal (PB0b/PB0c).

        * updater running -> stamp the cross-process liveness row (so a stale stamp
          means "dead", not "quiet") AND clear the recovery flag: the outage episode,
          if any, is over.
        * updater NOT running -> attempt exactly ONE bounded restart per episode via
          the RecoveryActuator (PB0c). ``_recovery_attempted`` gates re-entry so a
          persistently-dead updater is NOT hammered every tick; it re-arms only when
          the updater next reports running (a fresh episode).

        ponytail: webhook-mode deployments have no polling updater — the else-branch's
        restart is a logged no-op there; the live path here is long-poll.
        """
        app = self._bot_app
        updater = getattr(app, "updater", None) if app is not None else None
        if updater is not None and getattr(updater, "running", False):
            self._recovery_attempted = False
            if self._liveness is not None:
                await self._liveness.mark_alive(self.channel_name)
            return
        # Updater is down. Attempt one bounded self-heal per outage episode.
        if self._recovery_attempted:
            return
        self._recovery_attempted = True  # bound BEFORE attempting (no re-entry)
        await self._self_heal_polling()

    async def _self_heal_polling(self) -> None:
        """Hand the dead poll loop to the one RecoveryActuator for a bounded restart.

        The ladder is retry -> surrender (no reroute/substitute — nothing to reroute a
        single channel to), so exactly one restart is attempted and the outcome is
        re-verified: a "recovery" only counts if the updater actually comes back up.
        Never lets an exception escape — the heartbeat task must survive forever.
        """
        from stackowl.pipeline.recovery_actuator import Failure, RecoveryActuator

        log.telegram.debug("[telegram] adapter._self_heal_polling: entry — updater down")
        try:
            outcome = await RecoveryActuator().recover(
                Failure(name="channel:telegram", kind="channel", transient=True),
                attempt=self._restart_polling,
                verify=lambda _r: getattr(
                    getattr(self._bot_app, "updater", None), "running", False
                ),
            )
            if outcome.recovered:
                log.telegram.info(
                    "[telegram] adapter._self_heal_polling: telegram poll loop self-healed",
                    extra={"_fields": {"via": outcome.via}},
                )
            else:
                log.telegram.error(
                    "[telegram] adapter._self_heal_polling: telegram poll loop restart "
                    "surrendered — staying down; operator alert via health sweep",
                    extra={"_fields": {"rungs_tried": outcome.rungs_tried}},
                )
        except Exception as exc:  # noqa: BLE001 — heartbeat must never die
            log.telegram.error(
                "[telegram] adapter._self_heal_polling: self-heal raised — heartbeat continues",
                extra={"_fields": {"err": repr(exc)}},
            )

    async def _restart_polling(self) -> None:
        """Restart the long-poll updater in-place (the RecoveryActuator retry rung).

        Delegates to the ``_bot`` lifecycle helper so the ``start_polling`` params live
        in one place. No-op (warn) when there is no app/updater (webhook mode / not yet
        started) — a documented PB0b gap that must not crash the heartbeat.
        """
        log.telegram.debug("[telegram] adapter._restart_polling: entry")
        if self._bot_app is None:
            log.telegram.warning(
                "[telegram] adapter._restart_polling: no bot app — no-op (webhook / not started)"
            )
            return
        await restart_polling(self._bot_app)
        log.telegram.debug("[telegram] adapter._restart_polling: exit")

    async def _liveness_heartbeat(self) -> None:
        """Periodic stamp loop; cancelled cleanly in stop()."""
        log.telegram.debug("[telegram] adapter.liveness_heartbeat: entry")
        while True:
            await self._beat_once()
            await asyncio.sleep(HEARTBEAT_INTERVAL_S)

    def _on_liveness_task_done(self, task: asyncio.Task[None]) -> None:
        """Log loudly if the heartbeat crashed — never a silent fire-and-forget."""
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            log.telegram.error(
                "[telegram] adapter.liveness_heartbeat: crashed — receive-liveness signal lost",
                extra={"_fields": {"err": repr(exc)}},
            )

    async def stop(self) -> None:
        """Gracefully shut down the Telegram session."""
        log.telegram.debug("[telegram] adapter.stop: entry")
        if self._liveness_task is not None:
            self._liveness_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._liveness_task
            self._liveness_task = None
        await stop_bot(self._bot_app)
        log.telegram.debug("[telegram] adapter.stop: exit")

    async def receive(self) -> IngressMessage:
        """Yield the next IngressMessage enqueued by ``_handle_update``."""
        log.telegram.info("[telegram] adapter.receive: entry")
        t0 = time.monotonic()
        msg = await self._queue.get()
        log.telegram.info(
            "[telegram] adapter.receive: exit",
            extra={"_fields": {
                "trace_id": msg.trace_id, "text_len": len(msg.text),
                "duration_ms": (time.monotonic() - t0) * 1000,
            }},
        )
        return msg

    async def send(self, chunks: AsyncIterator[ResponseChunk]) -> None:
        """Collect streaming chunks, format, and dispatch to Telegram.

        ``kind="progress"`` chunks drive a single live status message
        (:class:`TelegramProgressView`) and are NEVER concatenated into the answer
        body. ``kind="answer"`` chunks (the default) accumulate and deliver exactly
        as before — with no progress chunks present, this method is byte-identical
        to the prior buffer-then-send behaviour.
        """
        log.telegram.info("[telegram] adapter.send: entry")
        TestModeGuard.assert_not_test_mode("telegram.send")
        buffer = ""
        # Per-turn delivery target: a turn's chunks all carry the SAME
        # `target` (the originating chat_id stamped at deliver-time). Capture it
        # so this turn replies to ITS OWN chat, not the shared `_last_chat_id`
        # (which a newer concurrent inbound update may have overwritten). None →
        # send_text falls back to `_last_chat_id` (single-terminal/back-compat).
        target: int | None = None
        actions: tuple[Action, ...] = ()
        # Raw inline-keyboard dict for approach-rating votes (Task 6) — Action-
        # shaped `actions` above cannot represent a callback_data payload, so
        # this is carried and dispatched separately (see below).
        raw_keyboard: dict[str, object] | None = None
        # Display-only text (e.g. Epic 3's token-usage footer) appended to the
        # OUTBOUND text only — never merged into `buffer`/`chunk.content`, so it
        # never reaches memory persistence (see ResponseChunk.display_suffix).
        display_suffix: str | None = None
        view: TelegramProgressView | None = None
        answer_started = False
        # Tracked across the chunk stream to backfill the retry_queue row for a
        # floored turn once the message is actually sent (below) — every chunk
        # in one turn shares the same trace_id, so any chunk's is enough.
        any_floor = False
        trace_id: str | None = None
        try:
            async for chunk in chunks:
                trace_id = getattr(chunk, "trace_id", None) or trace_id
                raw = chunk.target
                if isinstance(raw, str):
                    # Telegram only ever delivers int chat_id targets; a str (Slack
                    # channel/thread_ts) cannot reach the Telegram adapter by
                    # construction (each turn is delivered by its OWN channel adapter).
                    # Log loudly if one ever does, then fall back to _last_chat_id.
                    log.telegram.warning(
                        "[telegram] adapter.send: unexpected str target — falling back to _last_chat_id",
                        extra={"_fields": {"target": raw}},
                    )
                    target = None
                elif isinstance(raw, int):
                    target = raw

                if getattr(chunk, "kind", "answer") == "progress":
                    # Live status — render transiently; NEVER add to the answer buffer.
                    if target is not None:
                        if view is None:
                            view = self._make_progress_view(target)
                            view.start()  # background liveness ticker
                        await view.on_progress(chunk.content)
                    continue

                buffer += chunk.content
                any_floor = any_floor or getattr(chunk, "is_floor", False)
                chunk_actions = getattr(chunk, "actions", ())
                if chunk_actions:
                    actions = chunk_actions
                chunk_raw_keyboard = getattr(chunk, "raw_keyboard", None)
                if chunk_raw_keyboard is not None:
                    raw_keyboard = chunk_raw_keyboard
                chunk_display_suffix = getattr(chunk, "display_suffix", None)
                if chunk_display_suffix is not None:
                    display_suffix = chunk_display_suffix
                if view is not None and not answer_started:
                    view.on_first_answer()  # stop mutating the status; answer is here
                    answer_started = True
            # send_text is the single formatting chokepoint — pass RAW buffer, plus
            # any display-only suffix appended for THIS outbound send only (never
            # folded into `buffer`/chunk.content, so it never reaches persist_turn).
            # The answer is delivered as its own clean message(s), independent of
            # progress.
            outbound_text = buffer + (display_suffix or "")
            if raw_keyboard is not None:
                # Bypass send_text_or_actions/build_command_keyboard entirely — that
                # path is Action-shaped and cannot carry an approach-rating vote's
                # raw callback_data. send_inline_keyboard already accepts the same
                # raw dict shape InlineKeyboardBuilder.build() produces.
                try:
                    formatted = self._formatter.format_response(outbound_text)
                    message = cast(
                        "Message | None",
                        await self.send_inline_keyboard(formatted, raw_keyboard, chat_id=target),
                    )
                except Exception as exc:  # self-healing — never lose the answer
                    log.telegram.error(
                        "[telegram] adapter.send: raw_keyboard delivery failed — text fallback",
                        exc_info=exc,
                        extra={"_fields": {"trace_id": trace_id}},
                    )
                    message = await self.send_text_or_actions(outbound_text, actions, chat_id=target)
            else:
                message = await self.send_text_or_actions(outbound_text, actions, chat_id=target)
            if view is not None:
                await view.settle()  # collapse the status to a "✓ done in Ns" footer
            if any_floor and message is not None and trace_id is not None and target is not None:
                await self._backfill_floor_retry_row(trace_id, target, message.message_id)
            if raw_keyboard is not None and message is not None and trace_id is not None and target is not None:
                await self._backfill_approach_rating(trace_id, target, message.message_id)
        finally:
            # Safety net: never leak the ticker task if the loop raised mid-turn.
            if view is not None:
                await view.stop()
        log.telegram.info(
            "[telegram] adapter.send: exit",
            extra={"_fields": {"total_len": len(buffer), "explicit_target": target is not None,
                               "live_progress": view is not None}},
        )

    async def _backfill_floor_retry_row(
        self, trace_id: str, chat_id: int, message_id: int
    ) -> None:
        """Stamp the just-sent message ref onto the pending retry_queue row for a
        floored turn, so a later retry can edit THIS message instead of sending
        a duplicate. Best-effort: a backfill failure must never break delivery
        (the answer already landed) — log loudly and move on.
        """
        log.telegram.debug(
            "[telegram] adapter._backfill_floor_retry_row: entry",
            extra={"_fields": {
                "trace_id": trace_id, "chat_id": chat_id, "message_id": message_id,
            }},
        )
        from stackowl.pipeline.services import get_services

        retry_store = get_services().retry_queue_store
        if retry_store is None:
            log.telegram.debug(
                "[telegram] adapter._backfill_floor_retry_row: decision no_retry_store — no-op",
            )
            return
        try:
            await retry_store.backfill_channel_message(
                trace_id=trace_id, channel_chat_id=chat_id, channel_message_id=message_id,
            )
        except Exception as exc:  # backfill must never break delivery
            log.telegram.error(
                "[telegram] adapter._backfill_floor_retry_row: backfill failed",
                exc_info=exc,
                extra={"_fields": {"trace_id": trace_id}},
            )
            return
        log.telegram.debug(
            "[telegram] adapter._backfill_floor_retry_row: exit",
            extra={"_fields": {"trace_id": trace_id}},
        )

    async def _backfill_approach_rating(
        self, trace_id: str, chat_id: int, message_id: int
    ) -> None:
        """Stamp the just-sent message ref onto the pending approach-rating
        tracker entry (Task 5's ``consolidate.py`` calls ``record_pending``
        before a message id exists) so a tapped vote knows which message to
        edit. Best-effort: a tracker miss must never break delivery (the
        answer already landed) — log loudly and move on.
        """
        log.telegram.debug(
            "[telegram] adapter._backfill_approach_rating: entry",
            extra={"_fields": {
                "trace_id": trace_id, "chat_id": chat_id, "message_id": message_id,
            }},
        )
        from stackowl.pipeline.services import get_services

        tracker = get_services().approach_rating_tracker
        if tracker is None:
            log.telegram.debug(
                "[telegram] adapter._backfill_approach_rating: decision no_tracker — no-op",
            )
            return
        try:
            tracker.backfill_message(trace_id=trace_id, chat_id=chat_id, message_id=message_id)
        except Exception as exc:  # backfill must never break delivery
            log.telegram.error(
                "[telegram] adapter._backfill_approach_rating: backfill failed",
                exc_info=exc,
                extra={"_fields": {"trace_id": trace_id}},
            )
            return
        log.telegram.debug(
            "[telegram] adapter._backfill_approach_rating: exit",
            extra={"_fields": {"trace_id": trace_id}},
        )

    async def send_text_or_actions(
        self, text: str, actions: tuple[Action, ...], *, chat_id: int | None
    ) -> Message | None:
        """Deliver ``text`` as ONE message — with an inline keyboard attached
        when ``actions`` is non-empty, plain ``send_text`` otherwise.

        THE single chokepoint for "a reply that may itself carry tappable
        actions": used by :meth:`send` (a turn's answer) AND by
        :class:`~stackowl.channels.telegram.command_buttons.TelegramCommandButtonResolver`
        (a tapped command's reply, which may carry its OWN further actions) —
        both need the exact same shape (body text + keyboard together,
        self-healing to a plain text-only send if keyboard construction/
        delivery fails) so neither path double-sends ``text`` once raw via a
        keyboard message and once via :meth:`send_text`. Requires a resolved
        concrete chat for the keyboard path — the no-target contract for a
        bare text answer is unchanged when one isn't available.

        Returns the sent :class:`telegram.Message` (whichever branch fired) so
        callers can backfill a retry_queue row for floored turns.
        """
        resolved_chat = chat_id if chat_id is not None else self._last_chat_id
        if actions and resolved_chat is not None:
            try:
                from stackowl.channels.telegram.command_buttons import (
                    build_command_keyboard,
                    set_command_button_message_id,
                )

                keyboard, callback_ids = build_command_keyboard(resolved_chat, actions)
                formatted = self._formatter.format_response(text)
                # send_inline_keyboard is declared -> Any (python-telegram-bot's
                # send_message is itself untyped) — narrow to its documented
                # runtime contract (Message on success, None on no-target no-op).
                message = cast(
                    "Message | None",
                    await self.send_inline_keyboard(formatted, keyboard, chat_id=chat_id),
                )
                message_id = getattr(message, "message_id", None)
                if message_id is not None:
                    for callback_data in callback_ids:
                        set_command_button_message_id(callback_data, message_id)
                return message
            except Exception as exc:  # self-healing — never lose the answer
                log.telegram.error(
                    "[telegram] adapter.send_text_or_actions: action keyboard delivery failed — text fallback",
                    exc_info=exc,
                    extra={"_fields": {"n_actions": len(actions)}},
                )
        return await self.send_text(text, chat_id=chat_id)

    def _make_progress_view(self, chat_id: int) -> TelegramProgressView:
        """Build the per-turn live-status view bound to this adapter's I/O."""
        import time

        return TelegramProgressView(
            chat_id=chat_id,
            send_status=self.send_status,
            edit_status=self.edit_message,
            send_typing=self.send_typing,
            clock=time.monotonic,
            edit_min_interval_s=self._progress.telegram_edit_min_interval_s,
            typing_reissue_interval_s=self._progress.typing_reissue_interval_s,
            flicker_guard_s=self._progress.flicker_guard_ms / 1000.0,
            tick_interval_s=self._progress.tick_interval_s,
            elapsed_after_s=self._progress.elapsed_after_s,
            reassure_after_s=self._progress.reassure_after_s,
        )

    async def send_text(self, text: str, *, chat_id: int | None = _UNSET) -> Message | None:
        """Format RAW text then deliver — THE single outbound formatting chokepoint.

        Every caller of ``send_text`` (the on-turn reply, the proactive deliverer,
        clarify prompts, queue notices) passes RAW assistant markdown and gets
        consistent treatment: GFM tables flattened + MarkdownV2-escaped via
        :meth:`format_response`. No delivery path may bypass this — that bypass is
        exactly how proactive/notification table-corruption shipped. Callers that
        already hold MarkdownV2 (the specialized notification formatters) use
        :meth:`send_markdown` instead, to avoid double-escaping.

        Returns the sent :class:`telegram.Message` (see :meth:`_deliver`).
        """
        return await self._deliver(self._formatter.format_response(text), chat_id=chat_id)

    async def send_markdown(self, text: str, *, chat_id: int | None = _UNSET) -> None:
        """Deliver text that is ALREADY MarkdownV2 (pre-escaped by a specialized
        formatter). Skips :meth:`format_response` so per-field escaping is not
        double-applied. Use :meth:`send_text` for raw assistant output."""
        await self._deliver(text, chat_id=chat_id)

    async def _deliver(self, text: str, *, chat_id: int | None = _UNSET) -> Message | None:
        """Split (already-formatted) ``text`` per Telegram's limit and send each part.

        ``chat_id`` targets a specific chat (the per-message target threaded from
        ``IngressMessage.chat_id`` → ``ResponseChunk.target``); when omitted it
        falls back to ``self._last_chat_id`` for back-compat callers (proactive
        deliverer, clarify degrade-path). Resolving an EXPLICIT target here is what
        stops a concurrent turn from cross-delivering to whatever chat last sent an
        inbound update.

        No-target contract (C6 / C-1): an EXPLICIT ``chat_id`` (the on-turn
        ``send()`` path) that fails to resolve → log ``error`` + raise
        ``DeliveryError("telegram", "no_target")`` (a turn's answer is never
        silently dropped). ``chat_id`` OMITTED (proactive/best-effort) with no
        ``_last_chat_id`` → loud ``error``-level logged NO-OP, never a raise
        (preserves the proactive deliverer never-raises contract).

        Returns the LAST part's sent :class:`telegram.Message` (or ``None`` on
        the best-effort no-target no-op) — multi-part messages only backfill
        the final visible part; a documented limitation, not a bug.
        """
        explicit = chat_id is not _UNSET
        resolved = chat_id if explicit else None
        target = resolved if resolved is not None else self._last_chat_id
        log.telegram.debug(
            "[telegram] adapter.send_text: entry",
            extra={"_fields": {"text_len": len(text), "explicit_chat": explicit}},
        )
        TestModeGuard.assert_not_test_mode("telegram.send_text")
        if self._bot_app is None or target is None:
            # An explicit on-turn target that cannot resolve must fail loud — the
            # turn's answer would otherwise be silently lost. A resolved target
            # with a missing bot app is a no_channel; any other unresolvable
            # explicit target is a no_target. ``resolved is not None`` is load-
            # bearing here (NOT redundant): an explicit ``chat_id=None`` with no
            # bot app must still classify as no_target, matching the stray-narrow
            # contract (see test_send_str_target_narrows_to_none_and_raises).
            if explicit and resolved is not None and self._bot_app is None:
                log.telegram.error(
                    "[telegram] adapter.send_text: bot not initialised — failing loud",
                )
                raise DeliveryError("telegram", "no_channel")
            if explicit:
                log.telegram.error(
                    "[telegram] adapter.send_text: explicit target unresolvable — failing loud",
                    extra={"_fields": {"has_app": self._bot_app is not None}},
                )
                raise DeliveryError("telegram", "no_target")
            log.telegram.error(
                "[telegram] adapter.send_text: no active chat (best-effort) — message dropped",
                extra={"_fields": {"has_app": self._bot_app is not None}},
            )
            return None
        parts = self._splitter.split(text)
        log.telegram.debug(
            "[telegram] adapter.send_text: decision split",
            extra={"_fields": {"part_count": len(parts)}},
        )
        last_message: Message | None = None
        for idx, part in enumerate(parts):
            log.telegram.debug(
                "[telegram] adapter.send_text: step part_dispatched",
                extra={"_fields": {"idx": idx, "len": len(part)}},
            )
            last_message = await self._send_part(target, part, idx)
        log.telegram.debug("[telegram] adapter.send_text: exit")
        return last_message

    async def _send_part(self, target: int, part: str, idx: int) -> Message | None:
        """Send one message part, MarkdownV2-first with a plain-text fallback.

        Telegram rejects malformed MarkdownV2 with a ``BadRequest``. A formatter
        bug or an exotic character must NEVER cost the user the whole message —
        so on a parse rejection we log loudly (no hidden error) and re-send the
        SAME part as plain text. The content always lands; only the markup is
        sacrificed. Any non-parse error (network, auth, chat-not-found) is a real
        delivery failure and propagates unchanged.

        Returns the sent :class:`telegram.Message` (carries ``message_id``) so
        callers can backfill a retry_queue row for floored turns.
        """
        assert self._bot_app is not None  # caller guarantees a resolved app+target
        async with traced_span(log.telegram, "telegram.send_message", idx=idx, len=len(part)):
            try:
                # python-telegram-bot's send_message is untyped (returns Any) —
                # cast to the real runtime type (a telegram.Message on success,
                # it never returns None; raises on failure).
                return cast(
                    "Message",
                    await self._bot_app.bot.send_message(
                        chat_id=target,
                        text=part,
                        parse_mode="MarkdownV2",
                    ),
                )
            except BadRequest as exc:
                log.telegram.error(
                    "[telegram] adapter.send_text: MarkdownV2 rejected — retrying as plain text",
                    exc_info=exc,
                    extra={"_fields": {"idx": idx, "len": len(part)}},
                )
                return cast(
                    "Message",
                    await self._bot_app.bot.send_message(
                        chat_id=target,
                        text=part,
                        parse_mode=None,
                    ),
                )

    async def send_status(self, chat_id: int, text: str) -> int | None:
        """Send a plain live-status message and return its message_id (None on miss).

        Status text is sent raw (``parse_mode=None``) — it is short, glyph-led, and
        not assistant markdown. Best-effort: any Bot API failure is logged and
        returns None so a failed status send never breaks the turn.
        """
        TestModeGuard.assert_not_test_mode("telegram.send_status")
        if self._bot_app is None:
            return None
        try:
            msg = await self._bot_app.bot.send_message(
                chat_id=chat_id, text=text, parse_mode=None
            )
            return int(msg.message_id)
        except Exception as exc:  # noqa: BLE001 — progress is best-effort
            log.telegram.warning(
                "[telegram] adapter.send_status: failed — skipping live status",
                exc_info=exc,
                extra={"_fields": {"chat_id": chat_id}},
            )
            return None

    async def send_ephemeral(self, chat_id: str | int, text: str) -> int:
        """Send a silent (muted), self-identifying probe message.

        Unlike :meth:`send_status`, this MUST propagate any failure — a health
        probe that silently eats its own failure defeats the point of probing.
        Returns the sent message's id so the caller can immediately clean it up
        via :meth:`delete_message` once delivery is confirmed.
        """
        log.telegram.debug(
            "[telegram] adapter.send_ephemeral: entry",
            extra={"_fields": {"chat_id": chat_id, "text_len": len(text)}},
        )
        TestModeGuard.assert_not_test_mode("telegram.send_ephemeral")
        if self._bot_app is None:
            log.telegram.error(
                "[telegram] adapter.send_ephemeral: bot not initialised — failing loud",
            )
            raise DeliveryError("telegram", "no_channel")
        log.telegram.debug("[telegram] adapter.send_ephemeral: decision silent_send")
        msg = await self._bot_app.bot.send_message(
            chat_id=chat_id, text=text, parse_mode=None, disable_notification=True
        )
        message_id = int(msg.message_id)
        log.telegram.debug(
            "[telegram] adapter.send_ephemeral: exit",
            extra={"_fields": {"chat_id": chat_id, "message_id": message_id}},
        )
        return message_id

    async def send_typing(self, chat_id: int) -> None:
        """Issue the Telegram 'typing' chat action (auto-clears after ~5s).

        Best-effort: failures are logged and swallowed.
        """
        TestModeGuard.assert_not_test_mode("telegram.send_typing")
        if self._bot_app is None:
            return
        try:
            await self._bot_app.bot.send_chat_action(
                chat_id=chat_id, action=ChatAction.TYPING
            )
        except Exception as exc:  # noqa: BLE001 — progress is best-effort
            log.telegram.warning(
                "[telegram] adapter.send_typing: failed — continuing",
                exc_info=exc,
                extra={"_fields": {"chat_id": chat_id}},
            )

    async def send_inline_keyboard(
        self,
        text: str,
        keyboard: dict[str, object],
        chat_id: int | None = None,
        parse_mode: str | None = "MarkdownV2",
    ) -> Any:
        """Send a message with an inline keyboard attachment.

        ``chat_id`` targets a specific chat (e.g. the user who initiated a consent
        prompt); when omitted it falls back to the most-recent chat. Raises
        :class:`RuntimeError` when no target chat can be resolved so callers that
        require delivery (consent gate) can fail closed immediately.

        ``parse_mode`` defaults to ``"MarkdownV2"`` for callers that pass
        pre-formatted markdown (notifications). Pass ``None`` for raw text that
        must NOT be entity-parsed (e.g. a consent prompt containing a literal
        shell command) — plain text cannot trigger a Telegram 400 on unescaped
        ``.``/``-``/``=``/``/`` characters.

        Returns the sent :class:`telegram.Message` (carries ``message_id`` and
        ``chat.id``) so callers can later :meth:`edit_message` it — e.g. the
        consent gate rewrites the prompt to the chosen decision on tap. Returns
        ``None`` on the best-effort no-target path.
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
        send_kwargs: dict[str, object] = {
            "chat_id": target_chat,
            "text": text,
            "reply_markup": markup,
        }
        # Only set parse_mode when requested; None means "send as raw text" so an
        # unescaped command/path cannot 400 on entity parsing (consent prompts).
        if parse_mode is not None:
            send_kwargs["parse_mode"] = parse_mode
        message = await self._bot_app.bot.send_message(**send_kwargs)
        log.telegram.debug(
            "[telegram] adapter.send_inline_keyboard: exit",
            extra={"_fields": {"parse_mode": parse_mode}},
        )
        return message

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

    # File extensions routed to the richer Telegram media senders (lower-cased,
    # leading-dot). Anything else (incl. no extension) goes via send_document.
    _VIDEO_EXTS = frozenset({".mp4", ".mov", ".webm"})
    _PHOTO_EXTS = frozenset({".jpg", ".jpeg", ".png", ".gif"})

    async def send_file(
        self, file_path: str, caption: str | None = None, *, chat_id: int | None = _UNSET
    ) -> None:
        """Upload ``file_path`` to a chat, picking the media kind by extension.

        ``.mp4/.mov/.webm`` → ``bot.send_video``; ``.jpg/.jpeg/.png/.gif`` →
        ``bot.send_photo``; everything else → ``bot.send_document``. The file is
        opened in binary mode and passed to the Bot API; ``caption`` (optional)
        is attached. ``chat_id`` targets a specific chat (the proactive recipient
        threaded from the notification); when omitted it falls back to
        ``self._last_chat_id`` (same resolution as :meth:`send_text`). Resolving an
        EXPLICIT target here is what stops a proactive file send from
        cross-delivering to whatever chat last sent an inbound update.

        No-target contract (F-65, mirrors :meth:`send_text`): an EXPLICIT
        ``chat_id`` (the on-turn path) that cannot reach a live bot fails LOUD —
        log ``error`` + raise ``DeliveryError("telegram", "no_channel")`` (bot
        uninitialised) / ``"no_target"`` (unresolvable target) — so the file is
        never silently dropped while the ledger records a clean send; the
        :class:`ProactiveDeliverer` maps the raise to ``failed``. ``chat_id``
        OMITTED (proactive/best-effort) with no ``_last_chat_id`` → loud
        ``error``-level logged NO-OP, never a raise (preserves the proactive
        deliverer never-raises contract). On a send error the file handle is
        always closed and the exception propagates to the deliverer, which maps
        it to a structured ``failed`` — never a crash.
        """
        from pathlib import Path

        explicit = chat_id is not _UNSET
        resolved = chat_id if explicit else None
        target = resolved if resolved is not None else self._last_chat_id
        ext = Path(file_path).suffix.lower()
        log.telegram.debug(
            "[telegram] adapter.send_file: entry",
            extra={
                "_fields": {
                    "ext": ext,
                    "has_caption": bool(caption),
                    "explicit_chat": chat_id is not None,
                }
            },
        )
        TestModeGuard.assert_not_test_mode("telegram.send_file")
        if self._bot_app is None or target is None:
            # An explicit on-turn target that cannot reach a live bot must fail
            # loud — the file would otherwise be silently lost while the ledger
            # records a clean send (F-65). A resolved target with a missing bot
            # app is a no_channel; any other unresolvable explicit target is a
            # no_target. ``resolved is not None`` is load-bearing (NOT redundant):
            # an explicit ``chat_id=None`` with no bot must still be no_target.
            if explicit and resolved is not None and self._bot_app is None:
                log.telegram.error(
                    "[telegram] adapter.send_file: bot not initialised — failing loud",
                )
                raise DeliveryError("telegram", "no_channel")
            if explicit:
                log.telegram.error(
                    "[telegram] adapter.send_file: explicit target unresolvable — failing loud",
                    extra={"_fields": {"has_app": self._bot_app is not None}},
                )
                raise DeliveryError("telegram", "no_target")
            log.telegram.error(
                "[telegram] adapter.send_file: no active chat (best-effort) — file dropped",
                extra={"_fields": {"has_app": self._bot_app is not None}},
            )
            return

        # 2. DECISION — pick the Bot API media sender by extension.
        if ext in self._VIDEO_EXTS:
            sender, arg = self._bot_app.bot.send_video, "video"
        elif ext in self._PHOTO_EXTS:
            sender, arg = self._bot_app.bot.send_photo, "photo"
        else:
            sender, arg = self._bot_app.bot.send_document, "document"
        log.telegram.debug(
            "[telegram] adapter.send_file: decision media_kind",
            extra={"_fields": {"kind": arg}},
        )

        # 3. STEP — open in binary and upload; always close the handle.
        handle = open(file_path, "rb")  # noqa: SIM115 — closed in finally below
        try:
            kwargs: dict[str, Any] = {"chat_id": target, arg: handle}
            if caption:
                kwargs["caption"] = caption
            await sender(**kwargs)
        finally:
            handle.close()
        log.telegram.debug(
            "[telegram] adapter.send_file: exit",
            extra={"_fields": {"kind": arg, "chat_id": target}},
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

    async def edit_message(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        *,
        reply_markup: Any | None = None,
    ) -> bool:
        """Rewrite an existing message's text and (by default) drop its keyboard.

        Used to turn a consent prompt into a resolved decision after the user
        taps a button: ``reply_markup=None`` removes the inline keyboard so the
        message can't be re-tapped. ``text`` is sent raw (``parse_mode=None``) —
        a decision summary may contain literal command/path characters that
        MarkdownV2 would reject.

        Best-effort and fail-open: any Bot API failure is LOGGED and returns
        ``False`` rather than raising, so a failed cosmetic edit can never break
        a consent decision that has already been recorded. Telegram's benign
        "message is not modified" response (the new text equals the old) is
        treated as a no-op (debug log, returns ``False``).
        """
        log.telegram.debug(
            "[telegram] adapter.edit_message: entry",
            extra={"_fields": {
                "chat_id": chat_id, "message_id": message_id, "text_len": len(text),
                "removes_keyboard": reply_markup is None,
            }},
        )
        TestModeGuard.assert_not_test_mode("telegram.edit_message")
        if self._bot_app is None:
            log.telegram.warning("[telegram] adapter.edit_message: bot not initialised — skipped")
            return False
        try:
            await self._bot_app.bot.edit_message_text(
                text=text,
                chat_id=chat_id,
                message_id=message_id,
                parse_mode=None,
                reply_markup=reply_markup,
            )
        except Exception as exc:
            # "message is not modified" is benign — the new text equals the old,
            # so there is nothing to rewrite. Log at debug and carry on.
            if "not modified" in str(exc).lower():
                log.telegram.debug(
                    "[telegram] adapter.edit_message: not modified — benign no-op",
                    extra={"_fields": {"chat_id": chat_id, "message_id": message_id}},
                )
                return False
            # Any other failure is a cosmetic edit failure — log and fail open so
            # the (already-recorded) consent decision is never lost.
            log.telegram.error(
                "[telegram] adapter.edit_message: edit failed — fail open",
                exc_info=exc,
                extra={"_fields": {"chat_id": chat_id, "message_id": message_id}},
            )
            return False
        log.telegram.debug(
            "[telegram] adapter.edit_message: exit",
            extra={"_fields": {"chat_id": chat_id, "message_id": message_id}},
        )
        return True

    async def delete_message(self, chat_id: str | int, message_id: int) -> bool:
        """Best-effort delete of a previously-sent message (e.g. an ephemeral canary).

        Never raises — a delete failure is cosmetic cleanup, not a delivery
        failure. Returns ``True`` on success, ``False`` on any failure
        (including no bot app).
        """
        log.telegram.debug(
            "[telegram] adapter.delete_message: entry",
            extra={"_fields": {"chat_id": chat_id, "message_id": message_id}},
        )
        TestModeGuard.assert_not_test_mode("telegram.delete_message")
        if self._bot_app is None:
            log.telegram.warning(
                "[telegram] adapter.delete_message: bot not initialised — skipped"
            )
            return False
        try:
            await self._bot_app.bot.delete_message(chat_id=chat_id, message_id=message_id)
        except Exception as exc:  # noqa: BLE001 — cleanup is best-effort, never raises
            log.telegram.error(
                "[telegram] adapter.delete_message: delete failed — cosmetic, ignoring",
                exc_info=exc,
                extra={"_fields": {"chat_id": chat_id, "message_id": message_id}},
            )
            return False
        log.telegram.debug(
            "[telegram] adapter.delete_message: exit",
            extra={"_fields": {"chat_id": chat_id, "message_id": message_id}},
        )
        return True

    def set_voice_handler(self, handler: TelegramVoiceHandler) -> None:
        """Install the voice-transcription handler (must be called BEFORE start()).

        ``start()`` registers a ``filters.VOICE`` MessageHandler only when this is
        set, so a build with transcription disabled never wires the voice path.
        """
        self._voice_handler = handler
        log.telegram.debug("[telegram] adapter.set_voice_handler: installed")

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
        # STEER-1/F060 — a STRUCTURAL reply-to-the-bot link. When this message
        # replies to one of the BOT's own messages, stamp ``is_reply`` so the
        # orchestrator can fold it as a reply-to-inflight STEER (only when a turn is
        # actually running — ``resolve_reply_to_inflight``). We confirm the reply
        # target is the bot (``from_user.is_bot`` AND, when both usernames are
        # known, a username match) so a reply to ANOTHER user's message in a group
        # is never mistaken for a steer.
        is_reply_to_bot = False
        reply_to = getattr(message, "reply_to_message", None)
        if reply_to is not None:
            replied_user = getattr(reply_to, "from_user", None)
            if replied_user is not None and bool(getattr(replied_user, "is_bot", False)):
                replied_username = getattr(replied_user, "username", None)
                # If both usernames are known, require a match; otherwise trust
                # is_bot (a 1:1 DM with the bot has no other bot to confuse it).
                if (
                    not replied_username
                    or not self._bot_username
                    or str(replied_username).casefold()
                    == self._bot_username.casefold()
                ):
                    is_reply_to_bot = True
        ingress = IngressMessage(
            text=stripped,
            session_id=str(user_id),
            channel=self.channel_name,
            trace_id=_mint_request_id(),
            # Stamp the ORIGINATING chat on this message so its turn delivers back
            # to THIS chat — never the shared `_last_chat_id`, which a newer inbound
            # update may overwrite before this turn finishes (cross-deliver fix).
            chat_id=chat_id,
            is_reply=is_reply_to_bot,
            # ADR-D — only a private 1:1 DM enables bare-name vocative routing; a
            # group/supergroup/channel stays @Name-only to avoid human-name hijack.
            is_direct=(chat is not None and getattr(chat, "type", None) == "private"),
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

    # ----- HealableResource protocol (ADR-6 F-87, Task 4) -----------------------
    # Exposes the existing self-heal mechanism via HealableResource so the health
    # sweep can recycle the adapter on detection of a degraded update stream.

    @property
    def available(self) -> bool:
        """True if the update stream is currently healthy (ok status).

        ponytail: bare cached-state read, deliberately unlogged — matches every
        other HealableResource implementer in this codebase (EmbeddingRegistry,
        LanceDBAdapter, KuzuAdapter, DbPool: all bare `available` properties with
        no I/O). Called on every health-sweep tick and from `ensure_available()`
        itself; logging a hot-path property read would be noise, not signal. The
        state-changing path (`ensure_available()`) carries full 4-point logging.
        """
        now = time.monotonic()
        return (
            self._last_update_at is not None
            and now - self._last_update_at <= _UPDATE_DEGRADED_AFTER_S
        )

    @property
    def unavailable_reason(self) -> str | None:
        """Return the degradation message if unhealthy, else None."""
        # 1. ENTRY — implicit (property access)
        if self.available:
            return None
        # 2. DECISION — derive reason from health_check state (matches available logic)
        now = time.monotonic()
        if self._last_update_at is None:
            reason = "no update received yet"
        elif now - self._last_update_at > _UPDATE_DEGRADED_AFTER_S:
            reason = "update stream stale"
        else:
            # Should not reach here if available is False, but be defensive
            reason = "unknown degradation"
        log.telegram.debug(
            "[telegram] adapter.unavailable_reason: exit",
            extra={"_fields": {"reason": reason}},
        )
        # 4. EXIT
        return reason

    def _updater_running(self) -> bool:
        """Real transport liveness — the SAME check ``_beat_once()`` uses.

        Distinct from `available` (message-recency): a quiet-but-connected
        channel has `available=False` after ``_UPDATE_DEGRADED_AFTER_S`` with
        no inbound message, even though the long-poll updater is perfectly
        alive. Whole-branch review of the platform-wide self-heal arc caught
        this: gating `ensure_available()` on `available` alone made every
        idle-channel health-sweep tick call `_self_heal_polling()` and
        restart a healthy updater — the exact "heal-on-idle" restart-storm
        class this arc's WhatsApp task (round 2) already fought and fixed.
        """
        app = self._bot_app
        updater = getattr(app, "updater", None) if app is not None else None
        return updater is not None and getattr(updater, "running", False)

    async def ensure_available(self) -> None:
        """Recover a degraded adapter by calling _self_heal_polling() once.

        Gates on TRANSPORT liveness (`_updater_running()`), not `available`
        (message-recency) — a quiet-but-connected channel must never trigger
        a restart; only a genuinely down updater should. Guards re-entry with
        the SAME ``_recovery_attempted`` flag ``_beat_once()`` uses (the
        adapter's own ``_liveness_heartbeat`` timer ticks every
        HEARTBEAT_INTERVAL_S and can race a health-sweep-triggered call to this
        method). The check-and-set of ``_recovery_attempted`` below has no
        ``await`` between the read and the write, so it is atomic across
        concurrent coroutines on this single event loop — only one caller can
        ever pass it per outage episode, exactly like ``_beat_once()``.

        `_recovery_attempted` is reset back to False by `_beat_once()` once the
        updater is observed running again, so this does not "never heal again
        after first attempt" — a fresh episode re-arms the guard.
        """
        # 1. ENTRY
        log.telegram.debug(
            "[telegram] adapter.ensure_available: entry",
            extra={"_fields": {"available": self.available}},
        )
        # 2. DECISION — no-op if the transport is actually alive. Message
        # idleness alone (`available=False`) is NOT a heal trigger — it would
        # restart a perfectly healthy long-poll connection on every quiet
        # sweep tick.
        if self._updater_running():
            log.telegram.debug(
                "[telegram] adapter.ensure_available: updater running — no-op "
                "(transport alive; message idleness alone never triggers a heal)"
            )
            return
        # 3. STEP — atomic check-and-set on the SAME flag _beat_once() uses, so a
        # concurrent heartbeat tick and this call cannot both trigger recovery.
        if self._recovery_attempted:
            log.telegram.debug(
                "[telegram] adapter.ensure_available: recovery already attempted "
                "this outage episode — no-op (guarded by _recovery_attempted)"
            )
            return
        self._recovery_attempted = True  # bound BEFORE attempting (no re-entry)
        await self._self_heal_polling()
        # 4. EXIT
        log.telegram.info(
            "[telegram] adapter.ensure_available: exit — self-heal attempted"
        )

    def register_on_recycled(self, cb: Callable[[], None]) -> None:
        """No-op: the adapter's state is not cached downstream.

        Every caller re-acquires the adapter via ChannelRegistry or dependency
        injection, so there is no dead ref to clear on recycling. Matches the
        pattern in EmbeddingRegistry and LanceDBAdapter.
        """
        log.telegram.debug(
            "[telegram] adapter.register_on_recycled: no-op (no downstream dependents)"
        )

    def register_with_registry(self) -> None:
        """Self-register with the singleton :class:`ChannelRegistry`."""
        log.telegram.debug("[telegram] adapter.register_with_registry: entry")
        from stackowl.channels.registry import ChannelRegistry
        ChannelRegistry.instance().register(self)
        log.telegram.debug("[telegram] adapter.register_with_registry: exit")
