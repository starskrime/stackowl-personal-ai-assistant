"""Telegram command-button registry + callback resolver.

Mirrors ``channels/telegram/clarify.py``'s shape (a dedicated resolver class
registered on the shared CallbackRouter by prefix) but for CommandResponse
actions rather than parked clarify choices, and mirrors
``channels/telegram/consent.py``'s message-identity bookkeeping (register a
pending entry, backfill its ``message_id`` once the send returns) so a later
tap can rewrite the SAME message in place.

Telegram's chat_id IS the session_id for private chats (the clarify/consent
resolvers make the same assumption) — no separate session lookup is needed.

callback_data is ALWAYS routed through the short-id map below, even when a
command string would fit under Telegram's 64-byte limit directly — the map is
also how chat_id (and later, message_id) travel with the tap: the router's
handler signature is ``(callback_id, callback_data)`` only, it does not carry
the originating chat or message.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from stackowl.commands.response import CANCEL_SENTINEL, Action, make_confirm_response
from stackowl.infra.observability import log
from stackowl.pipeline.state import PipelineState

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.channels.telegram.adapter import TelegramChannelAdapter
    from stackowl.commands.registry import CommandRegistry

__all__ = [
    "TelegramCommandButtonResolver",
    "build_command_keyboard",
    "register_command_button",
    "set_command_button_message_id",
]

_CALLBACK_PREFIX = "cmd:"
_TTL_SECONDS = 15 * 60


@dataclass(slots=True)
class _PendingButton:
    """A registered button: who it's for, what it replays, and (once sent)
    the message it lives on — so a later tap can rewrite that SAME message."""

    chat_id: int
    action: Action
    expires_at: float
    message_id: int | None = None
    # Other short_ids registered as part of the SAME rendered keyboard group
    # (every button ``build_command_keyboard`` mints together — confirm's
    # Yes+Cancel pair, or an independent multi-choice row like an onboarding
    # step's [low][medium][high]) — resolving ONE pops the OTHERS too, so a
    # raced/replayed tap on a sibling can't fire after the group has already
    # been resolved (see handle_callback). Mirrors the TUI's row-level
    # ``_resolved`` latch (message_bubble.ActionButtonRow) — tapping any one
    # button in a rendered row freezes the whole row on both channels.
    sibling_ids: tuple[str, ...] = ()


# In-memory only (module-level) — a process restart drops any pending
# mapping; a very old unused button fails with a clear expired-message
# response rather than a silent no-op (see handle_callback).
# ponytail: no periodic sweep — entries are only evicted on tap (_pop_valid)
# or process restart. A never-tapped button leaks until restart; add a
# sweep task if button volume ever makes that measurable.
_button_map: dict[str, _PendingButton] = {}


def register_command_button(chat_id: int, action: Action) -> str:
    """Store (chat_id, action) under a fresh short id, return the callback_data."""
    short_id = secrets.token_urlsafe(6)
    _button_map[short_id] = _PendingButton(
        chat_id=chat_id, action=action, expires_at=time.monotonic() + _TTL_SECONDS
    )
    return f"{_CALLBACK_PREFIX}{short_id}"


def set_command_button_message_id(callback_data: str, message_id: int) -> None:
    """Backfill the sent message's id for a just-registered button.

    Called once the message carrying the button's keyboard has actually been
    sent (its id isn't known at registration time — the button must be
    registered first to build the keyboard). A miss (already tapped/expired,
    or a foreign prefix) is a silent no-op — the id is a cosmetic-edit aid
    only, never load-bearing for dispatch.
    """
    if not callback_data.startswith(_CALLBACK_PREFIX):
        return
    entry = _button_map.get(callback_data[len(_CALLBACK_PREFIX) :])
    if entry is not None:
        entry.message_id = message_id


def build_command_keyboard(
    chat_id: int, actions: tuple[Action, ...]
) -> tuple[dict[str, object], list[str]]:
    """Register one button per action, cross-link them as siblings, and build
    the Telegram keyboard dict.

    Every button rendered together on ONE message (a multi-choice row like
    ``[low][medium][high]``, or the confirm keyboard's Yes+Cancel pair) is a
    sibling group: resolving ANY one of them invalidates the OTHERS too (see
    ``_link_siblings`` and ``handle_callback``) — this is the Telegram side
    of the TUI's row-level ``_resolved`` latch (message_bubble.py). A
    single-action call is a no-op group of one (empty ``sibling_ids``).

    Returns the keyboard plus the callback_data list (in button order) so the
    caller can backfill each button's ``message_id`` once the send returns.
    """
    from stackowl.channels.telegram.keyboard import InlineKeyboardBuilder

    builder = InlineKeyboardBuilder()
    callback_ids: list[str] = []
    for action in actions:
        data = register_command_button(chat_id, action)
        builder.add_button(action.label, data)
        callback_ids.append(data)
    _link_siblings(callback_ids)
    return builder.build(), callback_ids


def _link_siblings(callback_ids: list[str]) -> None:
    """Cross-link a just-registered button GROUP (every button rendered
    together on one message) so resolving ONE pops the OTHERS too.

    Originally built for the confirm keyboard's Yes+Cancel pair (a 2-button
    group): without this, backfilling the confirm prompt's message_id closes
    the UI-visible half of the gap — Cancel's edit removes the keyboard, so
    Yes is no longer *rendered* — but the Yes entry would otherwise linger in
    ``_button_map`` for its full TTL, so a raced or directly-replayed tap on
    its callback_data could still dispatch the destructive action.

    Generalized to ANY group size: an independent multi-choice row (e.g. an
    onboarding step's ``[low][medium][high]``) has the same gap — tapping
    ``medium`` must invalidate the still-rendered ``low``/``high`` buttons on
    that same old message, not just the tapped one. A bare replay button
    (one entry, no group) gets an empty ``sibling_ids`` and this is a no-op.
    """
    short_ids = [cd[len(_CALLBACK_PREFIX) :] for cd in callback_ids]
    for sid in short_ids:
        entry = _button_map.get(sid)
        if entry is not None:
            entry.sibling_ids = tuple(s for s in short_ids if s != sid)


def _pop_valid(short_id: str) -> _PendingButton | None:
    entry = _button_map.pop(short_id, None)
    if entry is None:
        return None
    if time.monotonic() > entry.expires_at:
        return None
    return entry


class TelegramCommandButtonResolver:
    """Resolves a tapped command-replay button (prefix ``cmd:``)."""

    def __init__(self, adapter: TelegramChannelAdapter, registry: CommandRegistry | None) -> None:
        self._adapter = adapter
        self._registry = registry

    async def handle_callback(self, callback_id: str, callback_data: str) -> None:
        """Resolve a ``cmd:{short_id}`` tap: cancel, confirm-prompt, or dispatch.

        ``callback_id`` is accepted for signature parity with the router's
        handler contract (mirrors :class:`TelegramClarifyResolver`) and is not
        otherwise needed here — the router already acks the tap.
        """
        log.telegram.debug(
            "[telegram] command_buttons.handle_callback: entry",
            extra={"_fields": {"data_len": len(callback_data)}},
        )
        if not callback_data.startswith(_CALLBACK_PREFIX):
            return
        short_id = callback_data[len(_CALLBACK_PREFIX) :]
        entry = _pop_valid(short_id)
        if entry is None:
            log.telegram.info(
                "[telegram] command_buttons.handle_callback: expired or unknown button",
                extra={"_fields": {"short_id": short_id}},
            )
            return
        chat_id, action = entry.chat_id, entry.action
        if entry.sibling_ids:
            # This tap resolves the whole group (confirm Yes+Cancel) — pop the
            # sibling(s) too so a raced/replayed tap on them can't fire after
            # the group is already decided.
            for sid in entry.sibling_ids:
                _button_map.pop(sid, None)
            log.telegram.debug(
                "[telegram] command_buttons.handle_callback: invalidated sibling buttons",
                extra={"_fields": {"n_siblings": len(entry.sibling_ids)}},
            )

        if action.command == CANCEL_SENTINEL:
            await self._rewrite_or_send(chat_id, entry.message_id, "Cancelled.", None)
            log.telegram.debug(
                "[telegram] command_buttons.handle_callback: exit — cancelled",
            )
            return

        if action.destructive:
            confirm = make_confirm_response(action)
            # build_command_keyboard links the new Yes/Cancel pair as siblings
            # itself now — see _link_siblings.
            keyboard, callback_ids = build_command_keyboard(chat_id, confirm.actions)
            resolved_message_id = await self._rewrite_or_send(
                chat_id, entry.message_id, confirm.text, keyboard
            )
            # The confirm prompt lives on the SAME message (edit) or a fresh one
            # (fallback) — either way, backfill the NEW Yes/Cancel buttons with
            # the id of the message that now shows them. Without this, a later
            # Cancel tap has no message_id to edit, falls through to a FRESH
            # "Cancelled." send, and leaves the original message's Yes button
            # live and tappable — a real safety gap in the confirm flow.
            if resolved_message_id is not None:
                for cd in callback_ids:
                    set_command_button_message_id(cd, resolved_message_id)
            log.telegram.debug(
                "[telegram] command_buttons.handle_callback: exit — confirm prompt shown",
                extra={"_fields": {"n_buttons": len(callback_ids)}},
            )
            return

        # Non-destructive (or an already-confirmed second tap) — actually dispatch,
        # replaying the exact command string through the SAME CommandRegistry.dispatch
        # path a typed slash command uses.
        name, _, args = action.command.lstrip("/").partition(" ")
        state = PipelineState(
            trace_id=_new_trace_id(),
            session_id=str(chat_id),
            input_text=action.command,
            channel="telegram",
            owl_name="system",
            pipeline_step="start",
            interactive=True,
            reply_target=chat_id,
        )
        if self._registry is None:
            # Only reachable if this resolver was constructed without a registry
            # (production always passes CommandRegistry.instance() — see
            # orchestrator.py wiring). Fail loud rather than crash on None.dispatch.
            log.telegram.error(
                "[telegram] command_buttons.handle_callback: no registry wired — cannot dispatch",
                extra={"_fields": {"command": name}},
            )
            await self._adapter.send_text(
                "Sorry, this button can't be resolved right now.", chat_id=chat_id
            )
            return
        reply = await self._registry.dispatch(name, args, state)
        # Single formatted send whether or not the reply carries its own
        # actions — mirrors adapter.send()'s chunk-delivery shape exactly (see
        # TelegramChannelAdapter.send_text_or_actions) so a reply with actions
        # is never sent twice (once raw via send_text, once formatted via the
        # keyboard message).
        await self._adapter.send_text_or_actions(reply.text, reply.actions, chat_id=chat_id)
        log.telegram.debug(
            "[telegram] command_buttons.handle_callback: exit — dispatched",
            extra={"_fields": {"command": name}},
        )

    async def _rewrite_or_send(
        self,
        chat_id: int,
        message_id: int | None,
        text: str,
        keyboard: dict[str, object] | None,
    ) -> int | None:
        """Edit the tapped message in place when its id is known; else send fresh.

        Mirrors :class:`~stackowl.channels.telegram.consent.TelegramConsentPrompter`'s
        best-effort edit — a missing/stale ``message_id`` (e.g. the button was
        registered by a code path that never backfilled it) falls back to a new
        message so the interaction is never silently dropped.

        Returns the id of the message that NOW shows ``text``/``keyboard`` (the
        original ``message_id`` on the edit path, the freshly sent message's id
        on the fallback path, or ``None`` if it can't be determined) — the
        caller needs it to backfill any NEW buttons in ``keyboard`` (see the
        confirm-prompt path in :meth:`handle_callback`).
        """
        if message_id is not None:
            await self._adapter.edit_message(
                chat_id=chat_id, message_id=message_id, text=text, reply_markup=keyboard
            )
            return message_id
        if keyboard is not None:
            message = await self._adapter.send_inline_keyboard(text, keyboard, chat_id=chat_id)
            return getattr(message, "message_id", None)
        # send_text has no return (-> None) — nothing to backfill a sibling with.
        await self._adapter.send_text(text, chat_id=chat_id)
        return None


def _new_trace_id() -> str:
    import uuid as _uuid

    return _uuid.uuid4().hex
