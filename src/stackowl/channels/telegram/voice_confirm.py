"""Telegram voice-transcript confirm gate — Send / Discard before the pipeline.

When a voice note is transcribed we do NOT auto-inject it (the user explicitly
chose confirm-before-send). Instead the voice handler stores the transcript in a
:class:`PendingTranscriptStore` and shows it with a two-button inline keyboard
(✅ Send / 🗑 Discard). When the user taps a button the callback router calls
:meth:`VoiceConfirmHandler.handle_callback`:

* **send** → build an :class:`IngressMessage` (with ``chat_id`` STAMPED so the
  answer routes back to the originating chat under concurrency) and enqueue it —
  identical to a typed message from there on ("business as usual").
* **discard** → drop the pending transcript; nothing enters the pipeline.

Mirrors :mod:`stackowl.channels.telegram.consent` (the established inline-keyboard
round-trip): a ``dict`` keyed by a short id, ``{prefix}:{action}:{id}`` callback
data, a best-effort message edit after the decision is recorded. The router
(:class:`~stackowl.channels.telegram.callbacks.CallbackRouter`) already enforces
idempotency and acknowledges the tap, so this handler must NOT ack itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import uuid4

from stackowl.gateway.scanner import IngressMessage
from stackowl.infra.observability import log

if TYPE_CHECKING:
    from stackowl.channels.telegram.adapter import TelegramChannelAdapter

__all__ = ["CALLBACK_PREFIX", "PendingTranscriptStore", "VoiceConfirmHandler"]

# callback_data is "vtx:{action}:{id}"; a 16-hex id keeps it well under Telegram's
# 64-byte limit (enforced in keyboard.py:64).
CALLBACK_PREFIX = "vtx"


@dataclass(slots=True)
class _Pending:
    """A transcript awaiting the user's Send/Discard decision."""

    chat_id: int
    session_id: str
    transcript: str
    is_reply: bool
    message_id: int | None = None


class PendingTranscriptStore:
    """In-memory store of transcripts awaiting confirmation, keyed by a short id."""

    def __init__(self) -> None:
        self._pending: dict[str, _Pending] = {}

    def add(
        self, *, chat_id: int, session_id: str, transcript: str, is_reply: bool
    ) -> str:
        """Stash a transcript and return its short id (embedded in callback_data)."""
        rid = uuid4().hex[:16]
        self._pending[rid] = _Pending(
            chat_id=chat_id,
            session_id=session_id,
            transcript=transcript,
            is_reply=is_reply,
        )
        return rid

    def set_message_id(self, rid: str, message_id: int | None) -> None:
        """Record the sent prompt's message_id so a tap can edit it later."""
        pending = self._pending.get(rid)
        if pending is not None:
            pending.message_id = message_id

    def pop(self, rid: str) -> _Pending | None:
        """Remove and return the pending transcript for ``rid`` (None if gone)."""
        return self._pending.pop(rid, None)


class VoiceConfirmHandler:
    """Resolves ``vtx:{action}:{id}`` taps into an inject-or-drop decision."""

    def __init__(
        self,
        adapter: TelegramChannelAdapter,
        pending_store: PendingTranscriptStore,
    ) -> None:
        self._adapter = adapter
        self._pending = pending_store
        log.telegram.debug("[telegram] voice_confirm.init: entry")

    async def handle_callback(self, callback_id: str, callback_data: str) -> None:
        """Resolve a ``vtx:{action}:{id}`` callback. Never acks (the router does).

        4-point logging: entry / decision / step / exit.
        """
        log.telegram.debug(
            "[telegram] voice_confirm.handle_callback: entry",
            extra={"_fields": {"data_prefix": callback_data[:16]}},
        )
        parts = callback_data.split(":")
        if len(parts) != 3 or parts[0] != CALLBACK_PREFIX:
            log.telegram.debug(
                "[telegram] voice_confirm.handle_callback: not a voice callback — ignored"
            )
            return
        action, rid = parts[1], parts[2]
        pending = self._pending.pop(rid)
        if pending is None:
            # Already resolved / expired (e.g. a double tap raced the router).
            log.telegram.debug(
                "[telegram] voice_confirm.handle_callback: no live transcript — ignored",
                extra={"_fields": {"rid": rid}},
            )
            return

        if action == "send":
            await self._inject(pending)
        else:  # "discard" (or any non-send action) drops the transcript.
            log.telegram.info(
                "[telegram] voice_confirm.handle_callback: discarded",
                extra={"_fields": {"rid": rid}},
            )
            await self._edit(pending, "🗑")

        log.telegram.debug(
            "[telegram] voice_confirm.handle_callback: exit",
            extra={"_fields": {"action": action, "rid": rid}},
        )

    async def _inject(self, pending: _Pending) -> None:
        """Enqueue the confirmed transcript as a normal IngressMessage."""
        ingress = IngressMessage(
            text=pending.transcript,
            session_id=pending.session_id,
            channel=self._adapter.channel_name,
            trace_id=uuid4().hex,
            # STAMP chat_id (the cross-deliver fix the old auto-inject path omitted)
            # so the answer routes back to THIS chat under concurrency.
            chat_id=pending.chat_id,
            is_reply=pending.is_reply,
        )
        self._adapter._queue.put_nowait(ingress)
        self._adapter._last_chat_id = pending.chat_id
        log.telegram.info(
            "[telegram] voice_confirm._inject: enqueued",
            extra={"_fields": {"trace_id": ingress.trace_id, "text_len": len(pending.transcript)}},
        )
        # Best-effort: rewrite the prompt to show it was accepted (drop the keys).
        await self._edit(pending, f"✅ {pending.transcript}")

    async def _edit(self, pending: _Pending, text: str) -> None:
        """Best-effort prompt rewrite — a failed edit never loses the decision."""
        if pending.message_id is None:
            return
        try:
            await self._adapter.edit_message(
                pending.chat_id, pending.message_id, text, reply_markup=None
            )
        except Exception as exc:  # fail-open — decision already applied.
            log.telegram.error(
                "[telegram] voice_confirm._edit: message edit failed — decision kept",
                exc_info=exc,
                extra={"_fields": {"chat_id": pending.chat_id}},
            )
